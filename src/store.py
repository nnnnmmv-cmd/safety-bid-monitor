"""Supabase 데이터 레이어.

기존 yaml/sqlite 저장소를 대체. service_role 키로 RLS 우회 (서버사이드 전용).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from supabase import Client, create_client


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
    """INSERT ON CONFLICT DO NOTHING. 신규면 True."""
    record = {
        "notice_id": posting["notice_id"],
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
    res = client().table("bids").upsert(record, on_conflict="notice_id", ignore_duplicates=True).execute()
    return bool(res.data)


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


def upsert_sites(sites: list[dict[str, Any]]) -> None:
    """대시보드 표 일괄 저장. 이름(name)이 PK 역할."""
    if not sites:
        return
    payload = [_site_to_db(s) for s in sites if s.get("name")]
    if not payload:
        return
    client().table("sites").upsert(payload, on_conflict="name").execute()


def delete_site(name: str) -> None:
    client().table("sites").delete().eq("name", name).execute()


def replace_all_sites(sites: list[dict[str, Any]]) -> None:
    """명부 페이지의 '저장' 동작 — 입력에 없는 항목은 삭제."""
    existing = {row["name"] for row in list_sites()}
    incoming = {s["name"] for s in sites if s.get("name")}
    to_delete = existing - incoming
    if to_delete:
        client().table("sites").delete().in_("name", list(to_delete)).execute()
    upsert_sites(sites)


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
