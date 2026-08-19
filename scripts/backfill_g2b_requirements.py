"""나라장터 공고의 참가자격 요건(requirements)을 소급 추출한다.

사이클 추출(`g2b_requirements.run`)은 **마감 전 + relevance=likely**만 집는다.
그래서 이미 마감된 건은 영영 안 돌아간다 — 실측상 수의계약 중 뽑을 수 있는 97건이
전부 마감 지남으로 막혀 있었다. 이 스크립트는 그 두 필터를 빼고 훑는다.

쓰는 칸은 requirements / extract_status / extracted_at 셋뿐 (크롤러 단독 소유).
license_limit·region_limit은 **건드리지 않는다** — 그쪽은 허브 collectBids가
조달청 보조 API로 채우는 칸이라, 크롤러까지 쓰면 값의 출처를 가릴 수 없게 된다.

사용:
    .venv/bin/python scripts/backfill_g2b_requirements.py --dry-run
    .venv/bin/python scripts/backfill_g2b_requirements.py --limit 5
    .venv/bin/python scripts/backfill_g2b_requirements.py --only-sooui
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config

load_config()

from src import summarizer
from src.g2b_requirements import (
    WORK_ROOT, _call_llm, _doc_text, _hub_client, _pick_doc, _write_result,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0=전량")
    ap.add_argument("--only-sooui", action="store_true", help="수의계약만")
    ap.add_argument("--since", default="", metavar="YYYY-MM-DD",
                    help="이 날짜 이후 추출된 done 행을 다시 뽑는다 "
                         "(프롬프트를 고쳤을 때 그날 쓴 것만 되돌리는 용도)")
    args = ap.parse_args()

    client = _hub_client()
    rows = []
    for off in range(0, 20000, 1000):
        q = (
            client.table("arch_bid_notices")
            .select("bid_ntce_no, bid_ntce_ord, title, spec_docs, requirements, "
                    "extract_status, contract_method, extracted_at")
            .eq("source", "g2b").range(off, off + 999).execute().data or []
        )
        rows += q
        if len(q) < 1000:
            break

    def empty(r) -> bool:
        rq = r.get("requirements") or {}
        return not rq.get("region") and not rq.get("licenses")

    if args.since:
        # 프롬프트 교정 후 재추출 — 형식이 틀린 채로 쓰인 행만 정확히 겨냥한다
        targets = [
            r for r in rows
            if r.get("spec_docs") and str(r.get("extracted_at") or "")[:10] >= args.since
        ]
    else:
        targets = [r for r in rows if r.get("spec_docs") and empty(r)]
    if args.only_sooui:
        targets = [r for r in targets if "수의" in str(r.get("contract_method") or "")]
    total = len(targets)
    if args.limit:
        targets = targets[: args.limit]

    label = f"{args.since} 이후 추출분 재작업" if args.since else "첨부 있고 region·licenses 둘 다 빈 것"
    print(f"g2b {len(rows)}건 / {label} {total}건")
    print(f"  이번 대상 {len(targets)}건 · 계약방식 {dict(Counter(str(r.get('contract_method')) for r in targets))}")
    if args.dry_run:
        for r in targets[:12]:
            print(f"  [{r.get('contract_method')}] {r['title'][:58]}")
        return 0

    if not summarizer.check_auth():
        print("LLM 인증 실패 — 중단")
        return 1

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    reg = lic = nodoc = failed = 0
    misses: list[str] = []
    for i, t in enumerate(targets, 1):
        no, ord_, title = t["bid_ntce_no"], t["bid_ntce_ord"], t.get("title") or ""
        work = WORK_ROOT / re.sub(r"[^\w-]", "_", f"{no}_{ord_}")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            doc = _pick_doc(t.get("spec_docs") or [])
            if doc is None:
                _write_result(client, no, ord_, "no_doc", None)
                nodoc += 1
                continue
            text = _doc_text(doc, work)
            if not text.strip():
                _write_result(client, no, ord_, "no_doc", None)
                nodoc += 1
                misses.append(f"[문서 열림 실패] {title}")
                continue
            reqs = _call_llm(title, text)
            if reqs is None:
                _write_result(client, no, ord_, "failed", None)
                failed += 1
                misses.append(f"[LLM 실패] {title}")
                continue
            _write_result(client, no, ord_, "done", reqs)
            r_, l_ = reqs.get("region"), reqs.get("licenses")
            reg += bool(r_); lic += bool(l_)
            names = reqs.get("license_names") or []
            if not r_ and not l_:
                misses.append(f"[요건 언급 없음] {title}")
            print(f"  [{i}/{len(targets)}] ✅ 지역={str(r_ or '(없음)')[:30]} | "
                  f"licenses(원문) {len(l_ or [])}개 · license_names {len(names)}개")
            print(f"        {title[:56]}")
        except Exception as exc:
            failed += 1
            misses.append(f"[오류] {title}")
            print(f"  [{i}/{len(targets)}] ✗ {str(exc)[:50]} | {title[:34]}")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print(f"\n완료 — region {reg}건 / licenses {lic}건 / 문서없음 {nodoc}건 / 실패 {failed}건")
    if misses:
        print("못 뽑은 건:")
        for m in misses[:12]:
            print(f"  {m[:92]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
