"""사이트 설정 폼 payload 점검 — DB엔 select 한 번만, 쓰기 없음.

실행: .venv/bin/python scripts/test_crawler_payload.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_dotenv, PROJECT_ROOT
from src import store

REGISTRY_FIELDS: set[str] = {
    "homecheck", "hansijin", "hanjugum", "bidding_status", "category", "note", "memo",
    "new_submission_date", "period_start", "period_end", "announce_planned_date",
    "previous_announce_date", "previous_deadline",
}


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    row = store.get_site_crawler_row("오산시")
    assert row is not None, "오산시 줄 없음"
    print("표본:", row)
    assert row["pagination"].get("param") == "page", row["pagination"]

    # 명부 칸·name을 섞어 보내도 걸러져야 한다
    form = {k: row[k] for k in store.CRAWLER_FIELDS}
    form.update({"name": "바뀐이름", "memo": "x", "homecheck": "O", "period_end": "2099-01-01"})

    # 1) 아무것도 안 바꾸면 빈 payload
    assert store.crawler_update_payload(row, form) == {}, "안 바꿨는데 payload가 나옴"

    # 2) max_pages만 바꾸면 pagination만, 기존 param 'page'는 남는다
    form2 = {**form, "pagination": {"max_pages": 5}}
    p2 = store.crawler_update_payload(row, form2)
    print("max_pages만 변경:", p2)
    assert set(p2) == {"pagination"}, p2
    assert p2["pagination"]["param"] == "page" and p2["pagination"]["max_pages"] == 5, p2

    # 3) 셀렉터 한 키만 바꿔도 다른 셀렉터 키는 남는다
    db_sel = {**row, "selectors": {"detail_url_template": "https://x/{seq}"}}
    p3 = store.crawler_update_payload(db_sel, {**form, "selectors": {"row": "tr", "title": "", "date": ""}})
    print("셀렉터 row 추가:", p3)
    assert p3 == {"selectors": {"detail_url_template": "https://x/{seq}", "row": "tr"}}, p3

    # 4) 키는 크롤러 칸 안에서만, 명부 칸·memo·name은 절대 없음
    for p in (p2, p3):
        assert set(p) <= set(store.CRAWLER_FIELDS), p
        assert not (set(p) & (REGISTRY_FIELDS | {"name"})), p

    # DB에 pagination이 아예 없을 때만 화면 기본값이 깔린다
    p5 = store.crawler_update_payload({**row, "pagination": None}, {"pagination": {"max_pages": 2}})
    assert p5 == {"pagination": {"param": "pageIndex", "max_pages": 2}}, p5
    print("OK")


if __name__ == "__main__":
    main()
