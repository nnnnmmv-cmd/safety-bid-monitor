"""제목으로 만든 글 번호(`사이트::title=앞 40자`)의 겹침 판정 — DB를 직접 부르지 않는 순수 함수.

앞 40자가 같은 재공고·정정공고는 같은 번호가 되어 나중 글이 '이미 있음'으로 빠진다.
게시일이 다르면 `기본번호@YYYY-MM-DD` 꼬리 번호를 쓴다. 이미 저장된 글의 번호는 바꾸지 않는다.
게시일 비교는 날짜 글자 10자로만 한다 — datetime끼리 비교하면 aware/naive가 섞여 틀린다.
"""
from __future__ import annotations

from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

TITLE_MARK: str = "::title="


def is_list_address(url: str, list_url: str, list_params: dict[str, str], page_param: str) -> bool:
    """상세 URL 대신 목록 주소가 저장된 경우인가 (화성시-입찰공고처럼 상세 링크를 못 얻은 곳).

    경로가 list_url과 같고 쿼리 키가 목록 요청 키(list_params + 쪽 번호)뿐이면 목록 주소다.
    """
    u, l = urlsplit(url), urlsplit(list_url)
    if (u.scheme, u.netloc, u.path) != (l.scheme, l.netloc, l.path):
        return False
    allowed = set(parse_qs(l.query)) | set(list_params) | {page_param}
    return set(parse_qs(u.query, keep_blank_values=True)) <= allowed


def resolve_title_id(
    base_id: str,
    title: str,
    posted: str,
    url: str,
    list_address: bool,
    lookup: Callable[[str], dict[str, Any] | None],
) -> tuple[str, str]:
    """저장에 쓸 notice_id와 WARNING 문구(없으면 '')를 돌려준다.

    base_id: 어댑터가 만든 기본 번호. title= 번호가 아니면 그대로 돌려준다
    posted: 새 글 게시일 10자('YYYY-MM-DD'). 비면 기본 번호
    list_address: url이 그 사이트의 목록 주소인가 — 그렇다면 주소로 같은 글을 판정하지 않는다
    lookup: 번호 → 기존 행(posted_at·url·title) 또는 None. DB 조회 + 이번 회차 저장분
    """
    if TITLE_MARK not in base_id or not posted:
        return base_id, ""
    base = lookup(base_id)
    if base is None:
        return base_id, ""
    old = str(base.get("posted_at") or "")[:10]
    if not old:
        return base_id, ""
    if old == posted:
        if (base.get("title") or "") != title:
            return base_id, f"같은 날·같은 앞 40자·다른 제목 — 이미 있음으로 둠: 기존 '{base.get('title')}'"
        return base_id, ""
    # 게시판이 수정일을 게시일로 보여 주는 경우 — 같은 상세 주소면 같은 글
    if not list_address and url and (base.get("url") or "") == url:
        return base_id, f"게시일 바뀜: 기존 {old} → 새 {posted}"
    tail_id = f"{base_id}@{posted}"
    tail = lookup(tail_id)
    if tail is not None and (tail.get("title") or "") != title:
        return tail_id, f"같은 날·같은 앞 40자·다른 제목 — 이미 있음으로 둠: 기존 '{tail.get('title')}'"
    return tail_id, ""
