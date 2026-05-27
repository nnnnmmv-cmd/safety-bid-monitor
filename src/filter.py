from __future__ import annotations

from .adapters.base import BidPosting
from .config import KeywordConfig


def _normalize(s: str) -> str:
    # 공백 무시 매칭 — 키워드 '결과공고'가 제목 '선정 결과 공고'에도 잡히도록.
    # 띄어쓰기 변형(결과공고 / 결과 공고 / 결과  공고)을 키워드 한 줄로 흡수.
    return "".join((s or "").split())


def match_keywords(posting: BidPosting, keywords: KeywordConfig) -> list[str]:
    """매칭된 include 키워드 목록을 반환. 빈 리스트면 탈락.

    규칙 (정밀도 우선):
    1) 제목에 include 키워드가 있으면 → 제목의 exclude만 확인하고 통과 (본문은 무시)
    2) 제목에 include 없으면 → 본문에 include 있고, 제목·본문 어디에도 exclude 없으면 통과
    3) 둘 다 없으면 탈락
    매칭은 공백 무시 (한국 공고 제목의 띄어쓰기 변형 흡수).
    """
    title = _normalize(posting.title or "")
    body = _normalize(posting.body or "")
    use_title = "title" in keywords.require_match_in
    use_body = "body" in keywords.require_match_in

    title_includes: list[str] = [kw for kw in keywords.include if kw and use_title and _normalize(kw) in title]
    title_excludes: list[str] = [kw for kw in keywords.exclude if kw and use_title and _normalize(kw) in title]

    if title_includes:
        if title_excludes:
            return []
        return title_includes

    if not use_body:
        return []

    body_includes: list[str] = [kw for kw in keywords.include if kw and _normalize(kw) in body]
    if not body_includes:
        return []
    body_excludes: list[str] = [
        kw for kw in keywords.exclude
        if kw and (_normalize(kw) in title or _normalize(kw) in body)
    ]
    if body_excludes:
        return []
    return body_includes
