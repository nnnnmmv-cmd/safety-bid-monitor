"""건축 투찰 불가 발주청 알림 차단 셀프테스트 (실제 슬랙 발송 없음).

.venv/bin/python scripts/test_arch_block.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import notifier
from src.notifier import ARCH_NOTIFY_BLOCKED_SITES, should_skip_arch_notify

# (사이트, 제목, 스킵되어야 하나?, 설명)
CASES: list[tuple[str, str, bool, str]] = [
    # 1) 차단 대상 × 건축 → 스킵
    ("부천시", "건설공사(건축분야) 안전점검 수행기관 지정 공고(상동 317-8번지)", True, "부천시 건축 → 스킵"),
    ("양주시", "건축공사 안전점검 수행기관 지정 공고(근린생활시설 신축)", True, "양주시 건축 → 스킵"),
    ("과천시", "건설(건축)공사 안전점검 수행기관 지정 공고(과천행복드림센터)", True, "과천시 건축 → 스킵"),
    ("의왕시", "공동주택 건설공사 안전점검 수행기관 지정 공고", True, "의왕시 건축 → 스킵"),
    ("과천도시공사", "건축물 안전점검 수행기관 지정 공고", True, "과천도시공사 건축 → 스킵"),
    # 2) 차단 대상 × 토목 → 정상 발송 (토목은 절대 안 막는다)
    ("부천시", "건설공사 안전점검 수행기관 지정 공고(원미구 하수관로 정비공사)", False, "부천시 토목 → 발송"),
    ("양주시", "도로확포장공사 안전점검 수행기관 지정 공고", False, "양주시 토목 → 발송"),
    # 3) 차단 대상 × 분야 미표시 → 정상 발송 (모집·등록명부 추적 필요)
    ("부천시", "건설공사 안전점검 수행기관 모집 공고", False, "부천시 분야미표시 → 발송"),
    ("과천시", "안전점검 수행기관 등록명부 공고", False, "과천시 등록명부 → 발송"),
    # 4) 비대상 지자체 × 건축 → 정상 발송
    ("수원시", "건설공사(건축분야) 안전점검 수행기관 지정 공고", False, "수원시 건축 → 발송"),
    ("안산시", "건축공사 안전점검 수행기관 지정 모집 공고", False, "안산시 건축 → 발송"),
    # 5) 부분 문자열 오매칭 방지 — 남양주시는 양주시와 별개
    ("남양주시", "건축공사 안전점검 수행기관 지정 공고", False, "남양주시 건축 → 발송(양주시와 별개)"),
]


def main() -> int:
    print(f"차단 목록({len(ARCH_NOTIFY_BLOCKED_SITES)}곳): {', '.join(sorted(ARCH_NOTIFY_BLOCKED_SITES))}\n")
    failed = 0
    for site, title, expect, desc in CASES:
        got = should_skip_arch_notify(site, title)
        ok = got == expect
        failed += 0 if ok else 1
        mark = "⏭ 스킵" if got else "📨 발송"
        print(f"  {'✅' if ok else '❌'} {mark}  {desc}")
        if not ok:
            print(f"       기대={expect} 실제={got} | {site} | {title[:50]}")

    # send_one_posting 경로도 실제로 스킵되는지 (슬랙 호출 없이)
    print("\n[send_one_posting 경로 확인 — 슬랙 미호출]")
    calls: list[str] = []
    orig = notifier.send_card_with_attachments
    notifier.send_card_with_attachments = lambda *a, **k: calls.append("sent") or True
    try:
        class _S:
            bot_token, channel_all, channel_building, channel_civil = "x", "C0TEST", "", ""
        cfg = type("C", (), {"slack": _S()})()

        blocked = {"site_name": "부천시", "title": "건축공사 안전점검 수행기관 지정 공고",
                   "category": "토목", "extracted_fields": {}}
        r1 = notifier.send_one_posting(cfg, blocked, [])
        assert r1 is True and not calls, "차단 건은 슬랙 호출 없이 True(처리완료) 반환해야 함"
        print("  ✅ 부천시 건축 → 슬랙 호출 0회, 재시도 안 되게 True 반환")

        allowed = {"site_name": "부천시", "title": "하수관로 정비공사 안전점검 수행기관 지정 공고",
                   "category": "토목", "extracted_fields": {}}
        r2 = notifier.send_one_posting(cfg, allowed, [])
        assert r2 is True and len(calls) == 1, "토목 건은 정상 발송 경로를 타야 함"
        print("  ✅ 부천시 토목 → 슬랙 발송 경로 정상 진입")
    finally:
        notifier.send_card_with_attachments = orig

    print(f"\n{'전체 통과' if failed == 0 else f'❌ {failed}건 실패'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
