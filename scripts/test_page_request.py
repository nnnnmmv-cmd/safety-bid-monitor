"""page_request 점검 — 네트워크·DB 없음.

실행: .venv/bin/python scripts/test_page_request.py
SITES는 2026-10-20 sites(enabled=true 52곳)의 (name, pagination, list_params)를 그대로 옮긴 것.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.egov import page_request

SITES: list[tuple[str, dict, dict[str, str]]] = [('가평군', {'param': 'pageIndex', 'max_pages': 3}, {'key': '2148', 'not_ancmt_se_code': '01'}),
 ('경기도청', {'param': 'pageIndex', 'max_pages': 3}, {'bsIdx': '469', 'menuId': '1547'}),
 ('경기주택도시공사-조달청', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('고양시', {'param': 'pageIndex', 'max_pages': 3}, {'se': '01'}),
 ('과천시', {'param': 'page', 'max_pages': 3}, {'mId': '0301040000'}),
 ('광명시',
  {'param': 'q_currPage', 'max_pages': 1},
  {'q_searchVal': '안전', 'q_rowPerPage': '30', 'q_nftcBbsCode': '1001', 'q_searchKeyTy': '1001'}),
 ('광명시-입찰공고',
  {'param': 'q_currPage', 'max_pages': 1},
  {'q_searchVal': '안전', 'q_rowPerPage': '30', 'q_nftcBbsCode': '1003', 'q_searchKeyTy': '1001'}),
 ('광주시', {'param': 'page', 'max_pages': 3}, {'mId': '0202010000', 'searchTxt': '안전점검', 'searchPage': '50', 'searchType': 'tit'}),
 ('광주시-입찰정보',
  {'param': 'page', 'max_pages': 3},
  {'mId': '0202030000', 'seCode': '02', 'searchTxt': '안전점검', 'searchPage': '50', 'searchType': 'tit'}),
 ('구리시', {'param': 'pageIndex', 'max_pages': 3}, {'key': '387', 'searchGosiSe': '01,04,06'}),
 ('구리시-입찰정보', {'param': 'pageIndex', 'max_pages': 3}, {'key': '383', 'searchGosiSe': '02'}),
 ('국가철도공단', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('군포시',
  {'param': 'pageIndex', 'max_pages': 3},
  {'key': '3907', 'pageUnit': '50', 'searchCnd': 'all', 'searchKrwd': '안전', 'notAncmtSeCd': '01'}),
 ('군포시-입찰공고',
  {'param': 'pageIndex', 'max_pages': 3},
  {'key': '3903', 'pageUnit': '50', 'searchCnd': 'all', 'searchKrwd': '안전', 'notAncmtSeCd': '02'}),
 ('김포시', {'param': 'pageIndex', 'max_pages': 3}, {'key': '1004'}),
 ('남양주시', {'param': 'cpn', 'max_pages': 3}, {'key': '2492'}),
 ('동두천시', {'param': 'pageIndex', 'max_pages': 3}, {'key': '340', 'searchCnd': 'SJ', 'searchKrwd': '안전점검', 'not_ancmt_se_code': '04'}),
 ('동두천시-공고', {'param': 'pageIndex', 'max_pages': 3}, {'key': '340', 'searchCnd': 'SJ', 'searchKrwd': '안전점검', 'not_ancmt_se_code': '02'}),
 ('부천시', {'param': 'pageIndex', 'max_pages': 3}, {'menuid': '148002003001'}),
 ('성남시', {'param': 'curPage', 'max_pages': 3}, {}),
 ('성남시-입찰공고', {'param': 'curPage', 'max_pages': 3}, {}),
 ('수원시', {'param': 'q_currPage', 'max_pages': 3}, {'q_currPage': '1', 'q_searchKey': 'SJ', 'q_searchVal': '안전', 'q_rowPerPage': '50'}),
 ('시흥시', {'param': 'page', 'max_pages': 3}, {'mId': '0401040100', 'searchTxt': '안전점검', 'searchPage': '50', 'searchType': 'tit'}),
 ('시흥시-입찰정보',
  {'param': 'page', 'max_pages': 3},
  {'mId': '0401050000', 'seCode': '02', 'searchTxt': '수행기관', 'searchPage': '50', 'searchType': 'tit'}),
 ('안산시', {'param': 'currentPage', 'max_pages': 3}, {'key': '271', 'bbs_code': 'WWW13', 'sch_text': '안전점검', 'sch_type': 'sj'}),
 ('안산시-입찰공고', {'param': 'currentPage', 'max_pages': 3}, {'key': '271', 'bbs_code': 'WWW15', 'sch_text': '안전점검', 'sch_type': 'sj'}),
 ('안성시', {'param': 'page', 'max_pages': 3}, {'mId': '0501040000', 'token': '1758850352756'}),
 ('안양시',
  {'param': 'pageIndex', 'max_pages': 3},
  {'key': '4101', 'pageUnit': '50', 'searchCnd': 'SJ', 'searchKrwd': '안전', 'searchGosiSe': '01,04'}),
 ('안양시-입찰공고',
  {'param': 'pageIndex', 'max_pages': 3},
  {'key': '4103', 'pageUnit': '50', 'searchCnd': 'SJ', 'searchKrwd': '안전', 'notAncmtSeCode': '02'}),
 ('양주시', {'param': 'pageIndex', 'max_pages': 3}, {'key': '4075'}),
 ('양평군', {'param': 'pageIndex', 'max_pages': 3}, {'key': '1119', 'bbsNo': '5'}),
 ('여주시-입찰계약', {'param': 'pageIndex', 'max_pages': 3}, {'key': '354', 'bbsNo': '28', 'searchCnd': 'SJ', 'searchKrwd': '안전'}),
 ('여주시-조달청', {'param': 'pageIndex', 'max_pages': 3}, {'key': '413', 'pageUnit': '10', 'searchCnd': 'B_Subject', 'ofr_pageSize': '10'}),
 ('연천군 맑은물관리사업소', {'param': 'pageIndex', 'max_pages': 3}, {'key': '3393', 'not_ancmt_se_code': '01'}),
 ('오산시', {'param': 'page', 'max_pages': 3}, {'mId': '0302010000'}),
 ('용인도시공사', {'param': 'page', 'max_pages': 3}, {}),
 ('용인도시공사-입찰공고', {'param': 'page', 'max_pages': 3}, {}),
 ('용인시-건축', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('용인시-토목', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('의왕시', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('의정부시', {'param': 'pageIndex', 'max_pages': 3}, {'mId': '0301040000', 'seCode': '01'}),
 ('이천시',
  {'param': 'page', 'max_pages': 3},
  {'mid': '0402020000', 'seCode': '04', 'searchTxt': '안전점검', 'searchPage': '50', 'searchType': 'tit'}),
 ('파주시', {'param': 'q_currPage', 'max_pages': 3}, {'bbsCd': '1022', 'q_ctgCd': '4063'}),
 ('평택도시공사-입찰정보', {'param': 'pageIndex', 'max_pages': 3}, {'t_type': 't_4'}),
 ('평택도시공사-조달청', {'param': 'pageIndex', 'max_pages': 3}, {}),
 ('평택시', {'param': 'page', 'max_pages': 3}, {'mid': '0401020100', 'token': '1764650987211', 'seCode': '01'}),
 ('평택지방해양수산청', {'param': 'pageindex', 'max_pages': 3}, {'menuIdx': '2122'}),
 ('포천시-조달청', {'param': 'pageIndex', 'max_pages': 3}, {'key': '3712', 'notAncmtSeCode': '01'}),
 ('하남시', {'param': 'pageIndex', 'max_pages': 3}, {'key': '171', 'not_ancmt_se_code': '01,04'}),
 ('하남시-입찰공고', {'param': 'pageIndex', 'max_pages': 3}, {'key': '171', 'not_ancmt_se_code': '02'}),
 ('화성시', {'param': 'q_currPage', 'max_pages': 1}, {'q_sc': 'notAncmtSj', 'q_sv': '안전점검'}),
 ('화성시-입찰공고', {'param': 'q_currPage', 'max_pages': 1}, {'q_sc': 'notAncmtSj', 'q_sv': '안전점검'})]


def old_request(pagination: dict, list_params: dict[str, str], page: int) -> dict[str, str]:
    """옛 fetch()가 만들던 쿼리 — 새 키가 없는 곳은 이것과 같아야 한다."""
    params = dict(list_params)
    params[str(pagination.get("param", "pageIndex"))] = str(page)
    return params


def main() -> None:
    assert len(SITES) == 52
    for name, pag, lp in SITES:
        assert not ({"mode", "also"} & set(pag)), name
        before = dict(lp)
        for page in (1, 2, 3):
            assert page_request(pag, lp, page) == (old_request(pag, lp, page), 0), (name, page)
        assert lp == before, f"{name}: 입력 list_params를 건드리면 안 됨"

    hw = next(s for s in SITES if s[0] == "화성시")
    pag = {**hw[1], "also": ["q_cp"]}
    params, click = page_request(pag, hw[2], 2)
    assert params["q_currPage"] == params["q_cp"] == "2" and click == 0, params
    assert page_request(pag, hw[2], 1)[0]["q_cp"] == "1"

    gy = next(s for s in SITES if s[0] == "고양시")
    pag = {**gy[1], "mode": "click"}
    assert page_request(pag, gy[2], 1) == (old_request(gy[1], gy[2], 1), 0), "click 모드도 1쪽 요청은 그대로"
    for page in (2, 3):
        assert page_request(pag, gy[2], page) == (old_request(gy[1], gy[2], 1), page), page
    print("OK — 52곳 새 키 없음: 1~3쪽 요청이 옛 코드와 같음 / also·click 동작 확인")


if __name__ == "__main__":
    main()
