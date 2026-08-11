"""지자체 공고(bids)를 허브 arch_bid_notices에 source='local'로 upsert.

목적: 크롤러가 모은 지자체 공고를 허브 입찰 관리 화면(회사별 판정·담기·나의방·
마감 알림)에 합류시킨다. 허브는 이미 source='local'을 처리하도록 배포돼 있어
이 upsert만으로 붙는다.

- 기존 bids 저장은 그대로 (이중 저장 기간 허용)
- notified_at은 비워 둔다 — 허브 아침 알림은 이 칸이 빈 공고만 싣는다.
  2026-08-11 알림 일원화 전까지는 크롤러가 자체 슬랙을 보내며 이 칸을 채웠고,
  그래서 지자체 공고가 허브 알림에 한 번도 실리지 않았다. 지금은 허브가 알린다.
  과거 공고를 소급 upsert할 때만 notified_at을 넘긴다(이미 크롤러가 알린 건이라 재알림 방지)
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


# 나라장터에 같은 공고가 그대로 올라오는 게시판 — 허브엔 이미 source='g2b' 행이 들어와 있어
# local로 또 넣으면 채널에 같은 공고가 두 줄로 뜬다 (광주시 실측 3건, 제목 완전일치).
# 수집·bids 저장은 그대로 둔다 — 게시판 전용 건이 섞여 올 때 DB에는 남아 있어야 하고,
# 결과공고 선정업체 추출도 이 본문이 원천이다. 허브 upsert만 건너뛴다.
HUB_UPSERT_SKIP_SITES: frozenset[str] = frozenset({"광주시-입찰정보"})


def should_skip_hub(site_name: str | None) -> bool:
    """이 게시판 공고를 허브에 넣지 않는가 — 나라장터 중복 방지. 정확 일치."""
    return (site_name or "") in HUB_UPSERT_SKIP_SITES


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


# 접수기간 파싱 — '2026. 8. 7.(금) 9:00 ~ 8. 31.(월) 18:00' / '2026-07-07 09:00 ~ 2026-07-13 18:00' 등
_PERIOD_DATE_RE = re.compile(r"(?:(\d{4})\s*[.\-/]\s*)?(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*\.?")
_PERIOD_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
# 마감 시각이 안 적힌 공고는 업무 종료 시각으로 본다 (00:00로 두면 하루 일찍 만료 처리됨)
_DEFAULT_CLOSE_HOUR: int = 18


def parse_reg_deadline(bid_period: str | None, posted_at: Any = None) -> datetime | None:
    """공고문 접수기간 문자열 → 접수 마감 datetime. 애매하면 None.

    - '/' 뒤에 붙는 별도 일정(서류 접수일 등)은 접수마감이 아니므로 잘라낸다
    - 마지막 날짜를 마감일로, 그 뒤 마지막 시각을 마감 시각으로 (없으면 18:00)
    - 끝 날짜에 연도가 없으면 시작 연도를 물려받고, 시작보다 이르면 해를 넘긴 것으로 본다
    - 게시일보다 이르거나 400일 넘게 먼 값은 공고문 오기로 보고 버린다
    """
    if not bid_period:
        return None
    head = bid_period.split("/")[0]
    if not _PERIOD_DATE_RE.search(head):
        head = bid_period

    found: list[tuple[int, int | None, int, int]] = []
    for m in _PERIOD_DATE_RE.finditer(head):
        year, month, day = m.group(1), int(m.group(2)), int(m.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue  # 지번('466-2') 등 오매칭 방어
        found.append((m.end(), int(year) if year else None, month, day))
    if not found:
        return None

    pos, year, month, day = found[-1]
    if year is None:
        year = next((y for _, y, _, _ in found if y), None)
        if year is None:
            posted = _as_dt(posted_at)
            if posted is None:
                return None
            year = posted.year
        _, _, start_month, start_day = found[0]
        if (month, day) < (start_month, start_day):
            year += 1

    times = _PERIOD_TIME_RE.findall(head[pos:])
    hour, minute = (int(times[-1][0]), int(times[-1][1])) if times else (_DEFAULT_CLOSE_HOUR, 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        hour, minute = _DEFAULT_CLOSE_HOUR, 0

    try:
        end = datetime(year, month, day, hour, minute)
    except ValueError:
        return None

    posted = _as_dt(posted_at)
    if posted is not None:
        base = posted.replace(tzinfo=None)
        # 공고문에 연도를 잘못 적은 사례가 실제로 있어(2026 게시글에 2025 마감) 방어
        if end < base - timedelta(days=1) or end > base + timedelta(days=400):
            return None
    return end


def _reg_deadline(record: dict[str, Any], extracted: dict[str, Any] | None) -> str | None:
    """extracted_fields.bid_period에서 실제 접수 마감을 뽑아 ISO 문자열로."""
    end = parse_reg_deadline((extracted or {}).get("bid_period"), record.get("posted_at"))
    return end.isoformat() if end else None


# 링크 글자가 파일명이 아니라 버튼 문구인 게시판이 있다 (남양주시 '내려받기').
# 허브가 이름으로 공고문을 고르므로 그대로 두면 후보에서 탈락한다.
_GENERIC_LINK_LABELS: frozenset[str] = frozenset({
    "내려받기", "다운로드", "다운받기", "바로보기", "미리보기", "보기",
    "첨부파일", "첨부", "파일", "download", "view",
})
# URL에 원본 파일명을 담는 파라미터 (eminwon user_file_nm 계열)
_URL_NAME_KEYS: tuple[str, ...] = ("user_file_nm", "orgFileNm", "fileName", "fileNm", "originalName")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{2,5}$")


def _clean_doc_name(name: str, url: str) -> str:
    """첨부 표시명 정리 — 허브의 공고문 고르기가 이름에 걸려 있어 최대한 원본 파일명으로.

    게시판이 넣는 non-breaking space(안산시)를 보통 공백으로 펴고,
    버튼 문구이거나 확장자가 없으면 URL 파라미터에서 원본 파일명을 되찾는다.
    """
    name = " ".join((name or "").replace("\xa0", " ").split())
    if name and name.lower() not in _GENERIC_LINK_LABELS and _EXT_RE.search(name):
        return name
    from urllib.parse import parse_qs, unquote, urlparse

    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in _URL_NAME_KEYS:
        raw = (params.get(key) or [""])[0]
        cand = " ".join(unquote(raw).replace("\xa0", " ").split())
        if cand and _EXT_RE.search(cand):
            return cand
    base = " ".join(unquote(parsed.path.rsplit("/", 1)[-1]).split())
    if base and _EXT_RE.search(base):
        return base
    return name


def build_spec_docs(attachments: Any) -> list[dict[str, str]] | None:
    """첨부 목록 → 허브 spec_docs 형식 [{"url","name"}]. 게시 순서 유지, 없으면 None.

    허브가 이 이름으로 공고문을 골라 알림에 📎 링크를 붙인다(noticeDocLink).
    순서를 지켜야 하는 이유: 공고문이 첫 번째라는 보장이 없다(예산군 5개 중 5번째).
    attachments: Attachment 객체 또는 {"name","url"} dict의 리스트 둘 다 받는다.
    """
    docs: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in attachments or []:
        url = (getattr(a, "url", None) or (a.get("url") if isinstance(a, dict) else "")) or ""
        name = (getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else "")) or ""
        url, name = str(url).strip(), str(name).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        docs.append({"url": url, "name": _clean_doc_name(name, url)})
    return docs or None


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
    attachments: Any = None,
) -> dict[str, Any]:
    """bids 레코드 → arch_bid_notices 행.

    extracted: LLM 추출 결과(extracted_fields). inspection_cost로 기준금액을 채운다 —
    허브가 이걸 분모로 낙찰률을 계산하므로 지정·모집 공고에선 사실상 필수 값.
    notified_at: 넘기지 않으면 null — 허브가 이 공고를 아침 알림에 싣는다.
    이미 알림이 나간 과거 공고를 소급 upsert할 때만 넘긴다.
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
        # 공고문에 명시된 실제 접수 마감 — 있으면 허브가 게시일+14일 추정 대신 이걸 쓴다
        "reg_deadline_dt": _reg_deadline(record, extracted),
        "detail_url": record.get("url") or "",
        "relevance": _relevance(record),
        # 비워 둬야 허브 아침 알림에 실린다. 채우는 건 과거 공고 소급 upsert뿐
        "notified_at": notified_at,
        # 공고문 첨부 링크 — 허브 알림 줄의 📎공고문이 여기서 나온다
        "spec_docs": build_spec_docs(attachments),
    }


def upsert_bid(
    record: dict[str, Any],
    notified_at: str | None = None,
    extracted: dict[str, Any] | None = None,
    attachments: Any = None,
) -> bool:
    """공고 1건을 허브에 upsert. 실패해도 예외를 올리지 않는다(호출측 흐름 보호)."""
    try:
        if should_skip_hub(record.get("site_name")):
            logger.info("[hub] 나라장터 중복 게시판 — upsert 생략: %s",
                        str(record.get("title"))[:40])
            return False
        row = build_row(record, notified_at, extracted, attachments)
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


def update_spec_docs(notice_id: str, docs: list[dict[str, str]] | None) -> bool:
    """이미 들어간 허브 행의 공고문 첨부 링크만 갱신 (백필용)."""
    if not docs:
        return False
    try:
        (
            _hub_client().table("arch_bid_notices").update({"spec_docs": docs})
            .eq("source", SOURCE).eq("bid_ntce_no", notice_id).eq("bid_ntce_ord", ORD)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning("[hub] 첨부링크 갱신 실패 (%s): %s", notice_id[:40], exc)
        return False


def update_reg_deadline(notice_id: str, deadline_iso: str | None) -> bool:
    """이미 들어간 허브 행의 접수 마감만 갱신 (백필용)."""
    if not deadline_iso:
        return False
    try:
        (
            _hub_client().table("arch_bid_notices").update({"reg_deadline_dt": deadline_iso})
            .eq("source", SOURCE).eq("bid_ntce_no", notice_id).eq("bid_ntce_ord", ORD)
            .execute()
        )
        return True
    except Exception as exc:
        logger.warning("[hub] 접수마감 갱신 실패 (%s): %s", notice_id[:40], exc)
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
