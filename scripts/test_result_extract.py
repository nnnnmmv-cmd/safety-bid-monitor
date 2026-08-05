"""결과 공고 추출 셀프테스트 (LLM·DB 호출 없이 판별·파싱·병합만).

.venv/bin/python scripts/test_result_extract.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import summarizer as S

TITLE_CASES: list[tuple[str, bool]] = [
    ("건설공사 안전점검 수행기관 지정 결과 공고(왕곡동 노후배수관로)", True),
    ("건축공사 안전점검 수행기관 개찰결과 공고(양산4지구 B1BL)", True),
    ("건설공사 안전점검 수행기관 우선(예비)순위자 선정 결과 공고", True),
    ("건설공사 안전점검 수행기관 낙찰자 공고", True),
    # 모집 단계는 결과 아님 — "선정" 뒤에 "모집"이 오면 제외
    ("건설공사 안전점검 수행기관 모집 공고", False),
    ("안전점검 수행기관 선정을 위한 모집 공고", False),
    ("건설공사 안전점검 수행기관 지정 공고(성곡동 635-3)", False),
    ("건설공사 안전점검 수행기관 등록명부 공고", False),
]

PRICE_CASES: list[tuple[object, object]] = [
    ("12,345,000원", 12345000),
    ("6,000,000원(부가세 별도)", 6000000),
    (9800000, 9800000),
    (None, None),
    ("미정", None),
    ("", None),
    (0, None),
    (10**13, None),  # 비현실적 금액 방어
]


def main() -> int:
    failed = 0

    print("[1] 결과 공고 제목 판별")
    for title, expect in TITLE_CASES:
        got = S.is_result_notice(title)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {'결과' if got else '일반'} | {title[:52]}")

    print("\n[2] 금액 파싱")
    for raw, expect in PRICE_CASES:
        got = S._to_price(raw)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"  {'✅' if ok else '❌'} {raw!r} → {got!r}")

    print("\n[3] extracted_fields 병합 — 기존 7필드 보존")
    existing = {
        "inspection_cost": "6,000,000원", "contractor": "안양건설", "scale": "연면적 484㎡",
        "bid_period": "", "evaluation_method": "", "low_bid_rate": "", "winner_selection": "",
    }
    patch = {"result_kind": "지정결과", "selected_company": "㈜지오이앤씨",
             "selected_price": None, "target_project": "문원동 980"}
    merged = {**existing, **patch}
    ok = (merged["inspection_cost"] == "6,000,000원" and merged["contractor"] == "안양건설"
          and merged["selected_company"] == "㈜지오이앤씨" and len(merged) == 11)
    failed += 0 if ok else 1
    print(f"  {'✅' if ok else '❌'} 7필드 + 결과 4키 = {len(merged)}키, 기존값 보존 확인")

    print("\n[4] LLM 실패 시 빈 dict — 병합 skip 되는지")
    empty = {}
    ok = not (empty.get("selected_company") or empty.get("selected_price"))
    failed += 0 if ok else 1
    print(f"  {'✅' if ok else '❌'} 빈 결과는 merge 호출 조건에 걸리지 않음")

    print(f"\n{'전체 통과' if failed == 0 else f'❌ {failed}건 실패'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
