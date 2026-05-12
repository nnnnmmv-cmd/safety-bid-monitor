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
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", cleaned)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_price(text: str) -> int | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else None
    return int(m.group(1).replace(",", ""))


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
