"""제목 번호 겹침 판정 점검 — 네트워크·DB 없음. 표본은 2026-10-20 bids 실데이터.

실행: .venv/bin/python scripts/test_notice_id.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.notice_id import is_list_address, resolve_title_id

GH_BASE = "경기주택도시공사-조달청::title=경기고양 방송영상밸리 부지조성공사 안전점검 수행기관 지정공고"
GH_TITLE = "경기고양 방송영상밸리 부지조성공사 안전점검 수행기관 지정공고"
GH_0626 = {"title": GH_TITLE, "posted_at": "2026-06-26T00:00:00+00:00",
           "url": "https://www.gh.or.kr/gh/notice.do?mode=view&articleNo=64960&article.offset=0&articleLimit=10"}
GH_0825_URL = "https://www.gh.or.kr/gh/notice.do?mode=view&articleNo=65136&article.offset=0&articleLimit=50"

NYJ_BASE = "남양주시::title=건설공사 안전점검 수행기관 우선(예비) 순위자 선정 결과 공고[마석우리지"
NYJ_TITLE = "건설공사 안전점검 수행기관 우선(예비) 순위자 선정 결과 공고[마석우리지역주택조합 공동주택 신축공사]"
NYJ_ROW = {"title": NYJ_TITLE, "posted_at": "2026-10-19T00:00:00+00:00",
           "url": "https://www.nyj.go.kr/www/selectEminwonWebView.do?key=2492&pk=89530"}

HS_BASE = "화성시-입찰공고::title=건설공사 안전점검 수행기관 지정 공고"
HS_URL = "https://www.hscity.go.kr/www/gosi/BD_bidding.do?q_sc=notAncmtSj&q_sv=안전점검"
HS_ROW = {"title": "건설공사 안전점검 수행기관 지정 공고", "posted_at": "2026-10-08T00:00:00+00:00", "url": HS_URL}


def db(rows: dict[str, dict[str, Any]]):
    return rows.get


def main() -> None:
    # 목록 주소 판정 — 화성시-입찰공고는 목록 주소가 저장됨, 경기주택도시공사는 같은 경로라도 상세
    hs_listed = is_list_address(HS_URL, "https://www.hscity.go.kr/www/gosi/BD_bidding.do",
                                {"q_sc": "notAncmtSj", "q_sv": "안전점검"}, "q_currPage")
    gh_listed = is_list_address(GH_0825_URL, "https://www.gh.or.kr/gh/notice.do", {"articleLimit": "50"}, "pageIndex")
    assert hs_listed is True and gh_listed is False, (hs_listed, gh_listed)

    # 1) 경기주택도시공사 06-26(DB) ↔ 08-25(articleNo 다름) → 08-25는 꼬리 번호
    got = resolve_title_id(GH_BASE, GH_TITLE, "2026-08-25", GH_0825_URL, False, db({GH_BASE: GH_0626}))
    assert got == ("경기주택도시공사-조달청::title=경기고양 방송영상밸리 부지조성공사 안전점검 수행기관 지정공고@2026-08-25", ""), got
    # 06-26 자신은 목록 주소의 articleLimit만 달라도 게시일이 같으니 기본 번호
    got = resolve_title_id(GH_BASE, GH_TITLE, "2026-06-26", GH_0626["url"].replace("=10", "=50"), False, db({GH_BASE: GH_0626}))
    assert got == (GH_BASE, ""), got

    # 2) DB 문자열 '2026-10-19T00:00:00+00:00' vs datetime(2026,10,19) → 같은 글, 기본 번호
    posted = datetime(2026, 10, 19).strftime("%Y-%m-%d")
    got = resolve_title_id(NYJ_BASE, NYJ_TITLE, posted, NYJ_ROW["url"], False, db({NYJ_BASE: NYJ_ROW}))
    assert got == (NYJ_BASE, ""), got

    # 3) 기본 행 + 같은 날 다른 두 글: 첫째는 꼬리 번호, 둘째는 첫째(이번 회차 저장분)와 부딪혀 WARNING
    rows = {NYJ_BASE: NYJ_ROW}
    a_title = "건설공사 안전점검 수행기관 우선(예비) 순위자 선정 결과 공고[마석우리지역주택조합 공동주택 신축공사 정정]"
    b_title = "건설공사 안전점검 수행기관 우선(예비) 순위자 선정 결과 공고[마석우리지역주택조합 공동주택 신축공사 재공고]"
    first = resolve_title_id(NYJ_BASE, a_title, "2026-10-21", "https://www.nyj.go.kr/www/selectEminwonWebView.do?key=2492&pk=89600", False, db(rows))
    assert first == (NYJ_BASE + "@2026-10-21", ""), first
    rows[first[0]] = {"title": a_title, "posted_at": "2026-10-21", "url": "https://www.nyj.go.kr/www/selectEminwonWebView.do?key=2492&pk=89600"}
    second = resolve_title_id(NYJ_BASE, b_title, "2026-10-21", "https://www.nyj.go.kr/www/selectEminwonWebView.do?key=2492&pk=89601", False, db(rows))
    assert second[0] == "남양주시::title=건설공사 안전점검 수행기관 우선(예비) 순위자 선정 결과 공고[마석우리지@2026-10-21", second
    assert second[1].startswith("같은 날·같은 앞 40자·다른 제목"), second
    # 기본 행과 같은 날·다른 제목 → 기본 번호 + WARNING (이미 있음으로 둠)
    same_day = resolve_title_id(NYJ_BASE, a_title, "2026-10-19", "https://x/pk=1", False, db({NYJ_BASE: NYJ_ROW}))
    assert same_day[0] == NYJ_BASE and same_day[1].startswith("같은 날"), same_day

    # 4) 같은 입력을 두 번 넣어도 같은 번호
    args = (GH_BASE, GH_TITLE, "2026-08-25", GH_0825_URL, False, db({GH_BASE: GH_0626}))
    assert resolve_title_id(*args) == resolve_title_id(*args)

    # 5) 같은 상세 URL·다른 게시일 → 같은 글(기본 번호) + '게시일 바뀜'
    got = resolve_title_id(NYJ_BASE, NYJ_TITLE, "2026-10-22", NYJ_ROW["url"], False, db({NYJ_BASE: NYJ_ROW}))
    assert got == (NYJ_BASE, "게시일 바뀜: 기존 2026-10-19 → 새 2026-10-22"), got

    # 6) url = 목록 주소·다른 게시일 → 주소로 판정하지 않고 꼬리 번호 (화성시-입찰공고)
    got = resolve_title_id(HS_BASE, HS_ROW["title"], "2026-10-20", HS_URL, hs_listed, db({HS_BASE: HS_ROW}))
    assert got == ("화성시-입찰공고::title=건설공사 안전점검 수행기관 지정 공고@2026-10-20", ""), got

    # 7) 꼬리 번호가 이미 있고 제목이 같으면 그 번호 그대로 (insert_bid_if_new가 '이미 있음')
    tail = GH_BASE + "@2026-08-25"
    got = resolve_title_id(GH_BASE, GH_TITLE, "2026-08-25", GH_0825_URL, False,
                           db({GH_BASE: GH_0626, tail: {"title": GH_TITLE, "posted_at": "2026-08-25", "url": GH_0825_URL}}))
    assert got == (tail, ""), got

    # 8) 제목 번호가 아니거나 게시일이 비면 손대지 않음
    assert resolve_title_id("오산시::notAncmtMgtNo=1", "x", "2026-10-21", "u", False, db({})) == ("오산시::notAncmtMgtNo=1", "")
    assert resolve_title_id(GH_BASE, GH_TITLE, "", GH_0825_URL, False, db({GH_BASE: GH_0626})) == (GH_BASE, "")
    # DB 행 게시일이 비어 있어도 기본 번호
    assert resolve_title_id(GH_BASE, GH_TITLE, "2026-08-25", GH_0825_URL, False,
                            db({GH_BASE: {**GH_0626, "posted_at": None}})) == (GH_BASE, "")
    print("OK — 제목 번호 판정 표본 전부 통과")


if __name__ == "__main__":
    main()
