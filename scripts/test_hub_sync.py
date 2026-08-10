"""허브 upsert 매핑 셀프테스트 (DB 호출 없이 build_row만 검증).

.venv/bin/python scripts/test_hub_sync.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import hub_sync

POSTED = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def main() -> int:
    failed = 0

    def check(cond: bool, label: str) -> None:
        nonlocal failed
        failed += 0 if cond else 1
        print(f"  {'✅' if cond else '❌'} {label}")

    print("[1] 고정 컬럼 + 기본 매핑")
    rec = {
        "notice_id": "안산시::bbs_seq=1672222", "site_name": "안산시",
        "title": "건설공사 안전점검 수행기관 지정 모집 공고", "org": "안산시",
        "posted_at": POSTED, "deadline_at": None, "url": "https://ansan.go.kr/x",
        "estimated_price": 26646342, "matched_keywords": ["안전점검 수행기관"],
    }
    r = hub_sync.build_row(rec)
    check(r["source"] == "local", "source='local' 고정")
    check(r["bid_ntce_ord"] == "000", "bid_ntce_ord='000' 고정")
    check(r["bid_ntce_no"] == "안산시::bbs_seq=1672222", "bid_ntce_no = notice_id (사이트명 포함이라 충돌 없음)")
    check(r["institution"] == "안산시", "institution = org")
    check(r["presmpt_price"] == 26646342, "presmpt_price 숫자")
    check(r["detail_url"] == "https://ansan.go.kr/x", "detail_url = url")
    check(r["notice_dt"] == POSTED.isoformat(), "notice_dt = posted_at")

    print("\n[2] notified_at — 비면 허브 이중알림 발생")
    check(bool(r["notified_at"]), "기본값으로 현재 시각 채워짐")
    r2 = hub_sync.build_row(rec, notified_at="2026-08-05T00:00:00+00:00")
    check(r2["notified_at"] == "2026-08-05T00:00:00+00:00", "명시값 우선(백필 시 fetched_at 사용)")

    print("\n[3] bid_clse_dt — 마감 미상이면 게시일+14일")
    check(r["bid_clse_dt"] == (POSTED + timedelta(days=14)).isoformat(), "deadline_at 없음 → posted_at+14일")
    with_dl = dict(rec, deadline_at=datetime(2026, 8, 10, tzinfo=timezone.utc))
    check(hub_sync.build_row(with_dl)["bid_clse_dt"].startswith("2026-08-10"), "deadline_at 있으면 그대로")
    no_dates = dict(rec, posted_at=None, deadline_at=None)
    check(hub_sync.build_row(no_dates)["bid_clse_dt"] is None, "둘 다 없으면 null")

    print("\n[4] relevance — 결과공고는 입찰 기회가 아니므로 maybe")
    check(r["relevance"] == "likely", "제목에 수집 키워드 직접 매칭 → likely")
    result_rec = dict(rec, title="건설공사 안전점검 수행기관 지정 결과 공고(호원동)")
    check(hub_sync.build_row(result_rec)["relevance"] == "maybe", "결과 공고 → maybe")
    body_only = dict(rec, title="공사 관련 알림", matched_keywords=["안전점검 수행기관"])
    check(hub_sync.build_row(body_only)["relevance"] == "maybe", "본문에서만 매칭 → maybe")

    print("\n[5] 가격 방어")
    check(hub_sync.build_row(dict(rec, estimated_price=None))["presmpt_price"] is None, "None → null")
    check(hub_sync.build_row(dict(rec, estimated_price=0))["presmpt_price"] is None, "0 → null")

    print("\n[6] 안전점검비용 파싱 — 낙찰률의 분모 (실측 표기)")
    p = hub_sync.parse_inspection_cost
    for raw, expect, why in [
        ("6,600,000원(부가세포함)", 6600000, "기본"),
        ("115,892,240원 (VAT 별도)", 115892240, "VAT 별도"),
        ("금삼백만원(₩3,000,000원) (부가가치세 포함)", 3000000, "한글+기호 혼용"),
        ("20,000,000원(부가세 별도), 기초금액 19,400,000원(97% 적용)", 20000000, "복수금액 → 첫 금액(정가)"),
        ("6,000,000원 (정기안전점검 3회)", 6000000, "뒤 숫자에 오염 안 됨"),
        ("24,000천원, 내사천 16,000천원", None, "천원 단위 → 저장 안 함(1000배 오류 방지)"),
        ("3백만원(VAT별도)", None, "한글 금액만 → 저장 안 함"),
        ("", None, "빈 값"),
        (None, None, "None"),
    ]:
        check(p(raw) == expect, f"{why}: {str(raw)[:34]!r} → {p(raw)}")

    print("\n[7] 기준금액 채우기 — 결과공고에는 넣지 않는다")
    ex = {"inspection_cost": "9,000,000원(부가세 별도)"}
    check(hub_sync.build_row(rec, extracted=ex)["presmpt_price"] == 9000000, "지정공고 → inspection_cost 저장")
    res = dict(rec, title="건설공사 안전점검 수행기관 지정 결과 공고(정자동 117)")
    check(hub_sync.build_row(res, extracted=ex)["presmpt_price"] is None,
          "결과공고 → null (기준=낙찰이면 낙찰률 100%로 무의미)")
    no_ex = dict(rec, estimated_price=5000000)
    check(hub_sync.build_row(no_ex, extracted=None)["presmpt_price"] == 5000000,
          "LLM 추출 없으면 estimated_price 폴백")

    print(f"\n{'전체 통과' if failed == 0 else f'❌ {failed}건 실패'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
