"""Supabase 데이터 레이어.

기존 yaml/sqlite 저장소를 대체. service_role 키로 RLS 우회 (서버사이드 전용).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from .utils import utc_now_iso

logger: logging.Logger = logging.getLogger("safetybid.store")

HEALTH_TABLE: str = "bid_collection_health"


def log_site_health(
    site_name: str,
    reason: str,
    posts_saved: int,
    posts_fetched: int,
    run_at: str | None = None,
) -> bool:
    """게시판 1회 방문 기록. 실패해도 예외를 올리지 않는다(수집을 막지 않음).

    새 글이 0건이어도 남긴다 — 흔적이 없으면 게시판이 죽은 건지 원래 조용한 건지 구분이 안 된다.

    reason: 정상이면 'ok', 아니면 실패 사유
    posts_saved: 이번에 새로 저장한 글 수
    posts_fetched: 목록에서 읽어낸 글 수. 0이면 조용한 게 아니라 게시판이 안 읽히는 것
    """
    try:
        client().table(HEALTH_TABLE).insert({
            "site_name": site_name,
            "run_at": run_at or utc_now_iso(),
            "reason": reason[:300],
            "posts_saved": posts_saved,
            "posts_fetched": posts_fetched,
        }).execute()
        return True
    except Exception as exc:
        logger.warning("[health] 기록 실패 (%s): %s", site_name, str(exc)[:120])
        return False


def _client_or_none() -> Client | None:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


@lru_cache(maxsize=1)
def client() -> Client:
    c = _client_or_none()
    if c is None:
        raise RuntimeError(
            "Supabase 환경변수가 설정되지 않았습니다. "
            ".env에 SUPABASE_URL과 SUPABASE_SERVICE_KEY를 추가하세요."
        )
    return c


def is_configured() -> bool:
    return _client_or_none() is not None


# ============ bids ============

def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def insert_bid_if_new(posting: dict[str, Any]) -> bool:
    """SELECT 후 신규일 때만 INSERT. 신규면 True."""
    notice_id = posting["notice_id"]
    existing = client().table("bids").select("notice_id").eq("notice_id", notice_id).limit(1).execute()
    if existing.data:
        return False
    record = {
        "notice_id": notice_id,
        "site_name": posting["site_name"],
        "title": posting["title"],
        "org": posting.get("org"),
        "posted_at": _iso(posting.get("posted_at")),
        "deadline_at": _iso(posting.get("deadline_at")),
        "url": posting.get("url"),
        "estimated_price": posting.get("estimated_price"),
        "region": posting.get("region", ""),
        "matched_keywords": posting.get("matched_keywords") or [],
        "body_excerpt": (posting.get("body") or "")[:1000],
        "category": posting.get("category", ""),
        "fetched_at": posting.get("fetched_at") or datetime.utcnow().isoformat(),
        "notified": False,
    }
    try:
        client().table("bids").insert(record).execute()
        return True
    except Exception as exc:
        # 동시성 race condition — 다른 cron이 동시에 같은 글을 INSERT했을 때
        if "23505" in str(exc) or "duplicate" in str(exc).lower():
            return False
        raise


def fetch_unnotified() -> list[dict[str, Any]]:
    res = (
        client().table("bids")
        .select("*")
        .eq("notified", False)
        .order("site_name")
        .order("posted_at", desc=True)
        .execute()
    )
    return list(res.data or [])


def mark_notified(notice_ids: list[str]) -> None:
    if not notice_ids:
        return
    client().table("bids").update({"notified": True}).in_("notice_id", notice_ids).execute()


def update_bid_extracted_fields(notice_id: str, fields: dict[str, str]) -> None:
    """LLM 추출 7개 필드를 bids.extracted_fields(jsonb)에 저장."""
    client().table("bids").update({"extracted_fields": fields}).eq("notice_id", notice_id).execute()


def merge_bid_extracted_fields(notice_id: str, patch: dict[str, Any]) -> None:
    """extracted_fields에 patch 키만 병합 — 기존 키(7필드 등)는 보존.

    결과 공고 추출(result_kind·selected_company 등)을 나중에 덧붙일 때 사용.
    """
    if not patch:
        return
    c = client()
    res = c.table("bids").select("extracted_fields").eq("notice_id", notice_id).limit(1).execute()
    current = (res.data[0].get("extracted_fields") if res.data else None) or {}
    if not isinstance(current, dict):
        current = {}
    merged = {**current, **patch}
    c.table("bids").update({"extracted_fields": merged}).eq("notice_id", notice_id).execute()


def fetch_recent_bids(limit: int = 500) -> list[dict[str, Any]]:
    res = (
        client().table("bids").select("*")
        .order("posted_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(res.data or [])


def reset_notified_for_sites(site_names: list[str]) -> int:
    if not site_names:
        return 0
    res = client().table("bids").update({"notified": False}).in_("site_name", site_names).execute()
    return len(res.data or [])


def delete_all_bids() -> int:
    res = client().table("bids").delete().neq("notice_id", "").execute()
    return len(res.data or [])


# ============ sites ============

_SITE_DATE_FIELDS: tuple[str, ...] = (
    "last_updated", "new_submission_date", "period_start", "period_end",
    "announce_planned_date", "previous_announce_date", "previous_deadline",
)

_SITE_JSONB_FIELDS: tuple[str, ...] = ("list_params", "pagination", "selectors")


def _site_to_db(site: dict[str, Any]) -> dict[str, Any]:
    """대시보드 dict → Supabase 컬럼 형식."""
    record = dict(site)
    for f in _SITE_DATE_FIELDS:
        if record.get(f) in ("", None):
            record[f] = None
        elif isinstance(record[f], (date, datetime)):
            record[f] = record[f].isoformat() if isinstance(record[f], date) else record[f].isoformat()[:10]
    for f in _SITE_JSONB_FIELDS:
        if f not in record or record[f] is None:
            record[f] = {}
    return record


def list_sites() -> list[dict[str, Any]]:
    res = client().table("sites").select("*").order("name").execute()
    return list(res.data or [])


REGISTRY_MOVED_MSG: str = (
    "명부 정보(3사 O/X·유효기간·공고일·분류·비고·메모)는 허브에서 고칩니다: "
    "https://homecheck-sales-hub.vercel.app/arch/bids?tab=registry — 크롤러 설정은 대시보드 사이트 설정에서."
)

# 대시보드 사이트 설정 폼이 바꿀 수 있는 칸. 명부 칸·memo·name은 허브 소유라 넣지 않는다
CRAWLER_FIELDS: tuple[str, ...] = (
    "adapter", "base_url", "list_url", "list_params", "pagination", "selectors", "enabled",
)
_CRAWLER_DICT_FIELDS: tuple[str, ...] = ("pagination", "selectors")
_PAGINATION_DEFAULTS: dict[str, Any] = {"param": "pageIndex", "max_pages": 3}


# 2026-10-20 막음 — 명부는 허브가 고치므로, 행 전체를 덮는 upsert는 허브 수정분(날짜·memo·pagination)을 지운다
def upsert_sites(sites: list[dict[str, Any]]) -> None:
    """대시보드 표 일괄 저장. 이름(name)이 PK 역할."""
    raise RuntimeError(REGISTRY_MOVED_MSG)
    if not sites:
        return
    payload = [_site_to_db(s) for s in sites if s.get("name")]
    if not payload:
        return
    client().table("sites").upsert(payload, on_conflict="name").execute()


# 2026-10-20 막음 — 허브가 만든 명부 전용 줄까지 지울 수 있다
def delete_site(name: str) -> None:
    raise RuntimeError(REGISTRY_MOVED_MSG)
    client().table("sites").delete().eq("name", name).execute()


# 2026-10-20 막음 — 대시보드 표에 없는 줄(허브가 만든 줄)을 지우고 나머지를 옛 값으로 덮는다
def replace_all_sites(sites: list[dict[str, Any]]) -> None:
    """명부 페이지의 '저장' 동작 — 입력에 없는 항목은 삭제."""
    raise RuntimeError(REGISTRY_MOVED_MSG)
    existing = {row["name"] for row in list_sites()}
    incoming = {s["name"] for s in sites if s.get("name")}
    to_delete = existing - incoming
    if to_delete:
        client().table("sites").delete().in_("name", list(to_delete)).execute()
    upsert_sites(sites)


def get_site_crawler_row(name: str) -> dict[str, Any] | None:
    """사이트 설정 폼용 — 캐시 없이 그 줄의 name+크롤러 칸만 DB에서 다시 읽는다."""
    res = client().table("sites").select(",".join(("name",) + CRAWLER_FIELDS)).eq("name", name).execute()
    rows = list(res.data or [])
    return rows[0] if rows else None


def _norm(field: str, value: Any) -> Any:
    # 비교용 — None/''·{} 차이와 list_params 숫자/문자 차이는 같은 값으로 본다
    if field == "enabled":
        return bool(value)
    if field == "list_params":
        return {str(k): str(v) for k, v in (value or {}).items()}
    if field in _CRAWLER_DICT_FIELDS:
        return dict(value or {})
    return str(value or "")


def crawler_update_payload(db_row: dict[str, Any], form_values: dict[str, Any]) -> dict[str, Any]:
    """DB 값과 달라진 크롤러 칸만 돌려준다. DB를 부르지 않는 순수 함수.

    db_row: 저장 직전에 다시 읽은 DB 줄
    form_values: 폼 입력값. pagination·selectors는 바꾼 키만 담아도 되고, DB dict 위에 덮어 쓴다
        (화면 기본값 pageIndex·3은 DB에 값이 아예 없을 때만 깔린다)
    """
    payload: dict[str, Any] = {}
    for field in CRAWLER_FIELDS:
        if field not in form_values:
            continue
        new = form_values[field]
        if field == "pagination":
            new = {**(_PAGINATION_DEFAULTS if not db_row.get("pagination") else {}),
                   **(db_row.get("pagination") or {}), **(new or {})}
        elif field == "selectors":
            # 빈 문자열 키는 '지운다'는 뜻
            merged = {**(db_row.get("selectors") or {}), **(new or {})}
            new = {k: v for k, v in merged.items() if v not in ("", None)}
        if _norm(field, new) != _norm(field, db_row.get(field)):
            payload[field] = new
    return payload


def update_site_crawler_fields(name: str, changes: dict[str, Any]) -> int:
    """기존 줄의 크롤러 칸만 update. upsert/insert 대체 경로 없음. 바뀐 칸 수를 돌려준다."""
    bad = set(changes) - set(CRAWLER_FIELDS)
    if bad:
        raise ValueError(f"크롤러 칸이 아닌 칸은 못 바꿉니다: {sorted(bad)}")
    if not changes:
        return 0
    res = client().table("sites").update(changes).eq("name", name).execute()
    if len(res.data or []) != 1:
        raise RuntimeError(f"'{name}' 갱신 결과가 {len(res.data or [])}행 — 1행이어야 합니다. 이름을 확인하세요.")
    return len(changes)


class SiteExistsError(RuntimeError):
    pass


def insert_board_site(row: dict[str, Any]) -> None:
    """새 게시판 줄 추가 — name+크롤러 칸만 insert. 이미 있으면 덮지 않고 멈춘다."""
    name = str(row.get("name") or "").strip()
    if not name:
        raise ValueError("이름이 비었습니다.")
    bad = set(row) - {"name", *CRAWLER_FIELDS}
    if bad:
        raise ValueError(f"크롤러 칸이 아닌 칸은 못 넣습니다: {sorted(bad)}")
    msg = "이미 있는 줄입니다 — 기존 줄 고치기로 크롤러 칸만 바꾸세요"
    if get_site_crawler_row(name) is not None:
        raise SiteExistsError(msg)
    try:
        client().table("sites").insert({**row, "name": name}).execute()
    except Exception as exc:
        if "23505" in str(exc):
            raise SiteExistsError(msg) from exc
        raise


# ============ keywords ============

def list_keywords() -> dict[str, list[str]]:
    res = client().table("keywords").select("kind,value").execute()
    out: dict[str, list[str]] = {"include": [], "exclude": [], "match_in": []}
    for row in res.data or []:
        kind = row["kind"]
        if kind in out:
            out[kind].append(row["value"])
    return out


def replace_keywords(include: list[str], exclude: list[str], match_in: list[str]) -> None:
    client().table("keywords").delete().neq("id", 0).execute()
    rows: list[dict[str, str]] = []
    rows.extend({"kind": "include", "value": v} for v in include if v)
    rows.extend({"kind": "exclude", "value": v} for v in exclude if v)
    rows.extend({"kind": "match_in", "value": v} for v in match_in if v)
    if rows:
        client().table("keywords").insert(rows).execute()


# ============ app_users ============

def list_app_users() -> list[dict[str, Any]]:
    res = client().table("app_users").select("*").order("username").execute()
    return list(res.data or [])


def get_app_user(username: str) -> dict[str, Any] | None:
    res = client().table("app_users").select("*").eq("username", username).limit(1).execute()
    data = res.data or []
    return data[0] if data else None


def upsert_app_user(record: dict[str, Any]) -> None:
    client().table("app_users").upsert(record, on_conflict="username").execute()


def delete_app_user(username: str) -> None:
    client().table("app_users").delete().eq("username", username).execute()
