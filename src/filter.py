from __future__ import annotations

from .adapters.base import BidPosting
from .config import KeywordConfig


def match_keywords(posting: BidPosting, keywords: KeywordConfig) -> list[str]:
    """매칭된 include 키워드 목록을 반환. 빈 리스트면 탈락."""
    haystack_parts: list[str] = []
    if "title" in keywords.require_match_in:
        haystack_parts.append(posting.title or "")
    if "body" in keywords.require_match_in:
        haystack_parts.append(posting.body or "")
    haystack = "\n".join(haystack_parts)

    for bad in keywords.exclude:
        if bad and bad in haystack:
            return []

    matched: list[str] = [kw for kw in keywords.include if kw and kw in haystack]
    return matched
