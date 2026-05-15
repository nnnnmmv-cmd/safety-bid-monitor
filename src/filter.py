from __future__ import annotations

from .adapters.base import BidPosting
from .config import KeywordConfig


def match_keywords(posting: BidPosting, keywords: KeywordConfig) -> list[str]:
    """매칭된 include 키워드 목록을 반환. 빈 리스트면 탈락.

    규칙 (정밀도 우선):
    1) 제목에 include 키워드가 있으면 → 제목의 exclude만 확인하고 통과 (본문은 무시)
    2) 제목에 include 없으면 → 본문에 include 있고, 제목·본문 어디에도 exclude 없으면 통과
    3) 둘 다 없으면 탈락
    """
    title = posting.title or ""
    body = posting.body or ""
    use_title = "title" in keywords.require_match_in
    use_body = "body" in keywords.require_match_in

    title_includes: list[str] = [kw for kw in keywords.include if kw and use_title and kw in title]
    title_excludes: list[str] = [kw for kw in keywords.exclude if kw and use_title and kw in title]

    if title_includes:
        if title_excludes:
            return []
        return title_includes

    if not use_body:
        return []

    body_includes: list[str] = [kw for kw in keywords.include if kw and kw in body]
    if not body_includes:
        return []
    body_excludes: list[str] = [kw for kw in keywords.exclude if kw and (kw in title or kw in body)]
    if body_excludes:
        return []
    return body_includes
