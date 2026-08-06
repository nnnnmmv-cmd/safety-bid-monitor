"""지자체 공고(bids)를 허브 arch_bid_notices에 source='local'로 upsert.

목적: 크롤러가 모은 지자체 공고를 허브 입찰 관리 화면(회사별 판정·담기·나의방·
마감 알림)에 합류시킨다. 허브는 이미 source='local'을 처리하도록 배포돼 있어
이 upsert만으로 붙는다.

- 기존 bids 저장·자체 슬랙 알림은 그대로 (이중 저장 기간 허용)
- notified_at을 반드시 채운다 — 비우면 허브 아침 cron이 같은 공고를 또 알린다
- upsert는 (source, bid_ntce_no, bid_ntce_ord) 충돌 시 무시 → 재수집 멱등
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import summarizer
from .g2b_requirements import _hub_client

logger: logging.Logger = logging.getLogger("safetybid.hub")

SOURCE: str = "local"
ORD: str = "000"
# 지자체 게시판은 마감일을 본문에 안 적는 경우가 대부분(실측 284건 전부 미상).
# 마감이 null이면 허브 목록(마감 전 필터)에서 아예 안 보이므로 보수적 기본값을 준다.
DEFAULT_DEADLINE_DAYS: int = 14


def _iso(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _deadline(record: dict[str, Any]) -> str | None:
    """마감일 — 없으면 게시일+14일. 둘 다 없으면 null."""
    dl = _as_dt(record.get("deadline_at"))
    if dl:
        return dl.isoformat()
    posted = _as_dt(record.get("posted_at"))
    if posted:
        return (posted + timedelta(days=DEFAULT_DEADLINE_DAYS)).isoformat()
    return None


def _relevance(record: dict[str, Any]) -> str:
    """크롤러 판정 신뢰도 → likely / maybe.

    - 결과 공고(지정·선정 결과)는 이미 끝난 건이라 입찰 기회가 아님 → maybe
    - 제목에 수집 키워드가 직접 걸린 건 → likely (본문에서만 걸린 건은 maybe)
    """
    title = record.get("title") or ""
    if summarizer.is_result_notice(title):
        return "maybe"
    norm = "".join(title.split())
    matched = record.get("matched_keywords") or []
    if any("".join(str(k).split()) in norm for k in matched):
        return "likely"
    return "maybe"


def build_row(record: dict[str, Any], notified_at: str | None = None) -> dict[str, Any]:
    """bids 레코드 → arch_bid_notices 행."""
    price = record.get("estimated_price")
    return {
        "source": SOURCE,
        # notice_id는 이미 "사이트명::키=값" 형태라 사이트 간 충돌 없음
        "bid_ntce_no": str(record.get("notice_id") or "")[:200],
        "bid_ntce_ord": ORD,
        "title": record.get("title") or "",
        "institution": record.get("org") or record.get("site_name") or "",
        "presmpt_price": int(price) if isinstance(price, int) and price > 0 else None,
        "notice_dt": _iso(record.get("posted_at")),
        "bid_clse_dt": _deadline(record),
        "detail_url": record.get("url") or "",
        "relevance": _relevance(record),
        # 크롤러가 자체 슬랙 알림을 이미 보냈으므로 반드시 채운다 (허브 재알림 방지)
        "notified_at": notified_at or datetime.now(timezone.utc).isoformat(),
    }


def upsert_bid(record: dict[str, Any], notified_at: str | None = None) -> bool:
    """공고 1건을 허브에 upsert. 실패해도 예외를 올리지 않는다(호출측 흐름 보호)."""
    try:
        row = build_row(record, notified_at)
        if not row["bid_ntce_no"]:
            return False
        _hub_client().table("arch_bid_notices").upsert(
            row, on_conflict="source,bid_ntce_no,bid_ntce_ord", ignore_duplicates=True
        ).execute()
        return True
    except Exception as exc:
        logger.warning("[hub] upsert 실패 (%s): %s", str(record.get("notice_id"))[:40], exc)
        return False


def upsert_award(notice_id: str, company: str | None, price: int | None) -> bool:
    """결과 공고에서 뽑은 선정업체·금액을 같은 키의 허브 행에 반영."""
    patch: dict[str, Any] = {}
    if company:
        patch["award_winner"] = company
    if price:
        patch["award_amount"] = price
    if not patch:
        return False
    try:
        (
            _hub_client().table("arch_bid_notices").update(patch)
            .eq("source", SOURCE).eq("bid_ntce_no", notice_id).eq("bid_ntce_ord", ORD)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning("[hub] award 갱신 실패 (%s): %s", notice_id[:40], exc)
        return False
