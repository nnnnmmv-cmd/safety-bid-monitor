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
import re
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


_AMOUNT_RE = re.compile(r"([0-9][0-9,]*)\s*원")
# '24,000천원' 처럼 천원 단위로 적힌 공고 — 그대로 읽으면 1000배 어긋나므로 저장하지 않는다
_THOUSAND_WON_RE = re.compile(r"[0-9][0-9,]*\s*천\s*원")
# 안전점검 용역 현실 범위. 벗어나면 오인식으로 보고 버린다
_COST_MIN, _COST_MAX = 50_000, 10**11


def parse_inspection_cost(text: str | None) -> int | None:
    """공고문 안전점검비용 문자열 → 정수(원). 애매하면 None.

    실측 표기: '6,600,000원(부가세포함)', '금삼백만원(₩3,000,000원)',
    '20,000,000원(부가세 별도), 기초금액 19,400,000원(97% 적용)' 등.
    복수 금액이 적힌 건 첫 금액(= 안전점검비용 정가)을 쓴다 — 단일 금액 공고와 기준을 맞추기 위해.
    """
    if not text:
        return None
    if _THOUSAND_WON_RE.search(text):
        return None
    for m in _AMOUNT_RE.finditer(text):
        try:
            value = int(m.group(1).replace(",", ""))
        except (ValueError, OverflowError):
            continue
        if _COST_MIN <= value <= _COST_MAX:
            return value
    return None


def _base_price(record: dict[str, Any], extracted: dict[str, Any] | None) -> int | None:
    """허브 presmpt_price(기준금액) — 낙찰률 계산의 분모.

    결과 공고에는 넣지 않는다. 기준금액 칸에 낙찰금액이 들어가면 낙찰률이 100%로 나와 무의미.
    """
    if summarizer.is_result_notice(record.get("title") or ""):
        return None
    cost = parse_inspection_cost((extracted or {}).get("inspection_cost"))
    if cost:
        return cost
    # 폴백: 본문 정규식으로 뽑아둔 값 (LLM 추출이 없던 공고용)
    price = record.get("estimated_price")
    return int(price) if isinstance(price, int) and price > 0 else None


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


def build_row(
    record: dict[str, Any],
    notified_at: str | None = None,
    extracted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """bids 레코드 → arch_bid_notices 행.

    extracted: LLM 추출 결과(extracted_fields). inspection_cost로 기준금액을 채운다 —
    허브가 이걸 분모로 낙찰률을 계산하므로 지정·모집 공고에선 사실상 필수 값.
    """
    return {
        "source": SOURCE,
        # notice_id는 이미 "사이트명::키=값" 형태라 사이트 간 충돌 없음
        "bid_ntce_no": str(record.get("notice_id") or "")[:200],
        "bid_ntce_ord": ORD,
        "title": record.get("title") or "",
        "institution": record.get("org") or record.get("site_name") or "",
        "presmpt_price": _base_price(record, extracted),
        "notice_dt": _iso(record.get("posted_at")),
        "bid_clse_dt": _deadline(record),
        "detail_url": record.get("url") or "",
        "relevance": _relevance(record),
        # 크롤러가 자체 슬랙 알림을 이미 보냈으므로 반드시 채운다 (허브 재알림 방지)
        "notified_at": notified_at or datetime.now(timezone.utc).isoformat(),
    }


def upsert_bid(
    record: dict[str, Any],
    notified_at: str | None = None,
    extracted: dict[str, Any] | None = None,
) -> bool:
    """공고 1건을 허브에 upsert. 실패해도 예외를 올리지 않는다(호출측 흐름 보호)."""
    try:
        row = build_row(record, notified_at, extracted)
        if not row["bid_ntce_no"]:
            return False
        _hub_client().table("arch_bid_notices").upsert(
            row, on_conflict="source,bid_ntce_no,bid_ntce_ord", ignore_duplicates=True
        ).execute()
        return True
    except Exception as exc:
        logger.warning("[hub] upsert 실패 (%s): %s", str(record.get("notice_id"))[:40], exc)
        return False


def update_base_price(notice_id: str, price: int | None) -> bool:
    """이미 들어간 허브 행의 기준금액만 갱신.

    upsert는 ignore_duplicates라 기존 행을 안 건드리므로, 뒤늦게 확보한
    안전점검비용을 채울 땐 이 함수를 쓴다.
    """
    if not price:
        return False
    try:
        (
            _hub_client().table("arch_bid_notices").update({"presmpt_price": price})
            .eq("source", SOURCE).eq("bid_ntce_no", notice_id).eq("bid_ntce_ord", ORD)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning("[hub] 기준금액 갱신 실패 (%s): %s", notice_id[:40], exc)
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
