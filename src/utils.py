from __future__ import annotations

import re
from datetime import datetime, timezone

_DATE_PATTERNS: list[str] = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
]

_PRICE_RE: re.Pattern[str] = re.compile(r"([\d,]+)\s*원")


def parse_date(text: str) -> datetime | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    # 4자리 연도 (YYYY.MM.DD / YYYY-MM-DD / YYYY/MM/DD)
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", cleaned)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    # 2자리 연도 (YY.MM.DD) — 경기주택도시공사 등에서 사용. 70 이상이면 1900년대, 미만이면 2000년대.
    m = re.search(r"\b(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b", cleaned)
    if m:
        yy = int(m.group(1))
        year = 2000 + yy if yy < 70 else 1900 + yy
        try:
            return datetime(year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_price(text: str) -> int | None:
    """'원' 단위가 명시된 가격만 추출. 명시 없으면 None (전체 숫자 합치는 버그 방지)."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        value = int(m.group(1).replace(",", ""))
    except (ValueError, OverflowError):
        return None
    # bigint 안전 범위: 1조원(10**12) 초과는 명백한 오인식
    if value > 10**12 or value < 0:
        return None
    return value


def format_price(value: int | None) -> str:
    if value is None:
        return "미정"
    return f"{value:,}원"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def d_day_label(target: datetime | None, base: datetime | None = None) -> str:
    if target is None:
        return ""
    ref = base or datetime.now()
    delta = (target.date() - ref.date()).days
    if delta == 0:
        return "D-Day"
    if delta > 0:
        return f"D-{delta}"
    return f"D+{-delta}"
