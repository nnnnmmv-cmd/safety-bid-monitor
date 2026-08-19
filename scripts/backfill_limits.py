"""허브 지자체 공고에 면허(license_limit)·참가지역(region_limit)을 뒤늦게 채운다.

이 둘은 조달청 목록·보조 API에 안 실리고 첨부 공고문 안에만 있다.
detail + 첨부를 다시 열어 본문을 만들고 LLM으로 뽑는다 (수집 시점과 같은 경로).

허브 규칙: NULL='아직 안 봤다', ''='봤는데 제한 없음'. 그래서
LLM이 실패한 건은 아무것도 쓰지 않고 NULL로 남긴다 (hub_sync._limit_cols가 판단).
이미 값이 있는 칸은 덮지 않는다 (hub_sync.update_limits가 .is_(칸,"null")로 건다).

재실행 안전 — 이미 채워진 행은 대상에서 빠지므로 중단 후 다시 돌리면 이어서 한다.

사용:
    .venv/bin/python scripts/backfill_limits.py --dry-run
    .venv/bin/python scripts/backfill_limits.py --limit 3
    .venv/bin/python scripts/backfill_limits.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import load_config

load_config()

from src import hub_sync, store, summarizer
from backfill_results import _fresh_body, _resolve_site, store_runtime  # noqa: E402


def _site_map():
    from src.config import _site_row_to_config

    return {r["name"]: _site_row_to_config(r) for r in store.list_sites()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0=전량")
    args = ap.parse_args()

    hub = []
    for off in range(0, 20000, 1000):
        q = (
            hub_sync._hub_client().table("arch_bid_notices")
            .select("bid_ntce_no, title, license_limit, region_limit")
            .eq("source", hub_sync.SOURCE).range(off, off + 999).execute().data or []
        )
        hub += q
        if len(q) < 1000:
            break
    todo_ids = [
        r["bid_ntce_no"] for r in hub
        if r.get("license_limit") is None or r.get("region_limit") is None
    ]

    rows = store.client().table("bids").select(
        "notice_id, site_name, title, url, body_excerpt, extracted_fields"
    ).limit(2000).execute().data or []
    by_id = {r["notice_id"]: r for r in rows}

    targets = [by_id[i] for i in todo_ids if i in by_id]
    # 목록 URL만 있는 옛 row=N 행은 글 단위로 되돌아갈 수 없다
    skipped = [r for r in targets if "::row=" in r["notice_id"]]
    targets = [r for r in targets if "::row=" not in r["notice_id"]]
    total = len(targets)
    if args.limit:
        targets = targets[: args.limit]

    print(f"허브 local {len(hub)}건 / 면허·지역 중 하나라도 NULL {len(todo_ids)}건")
    print(f"  재조회 불가(목록URL) {len(skipped)}건 · 이번 대상 {len(targets)}/{total}건")
    if args.dry_run:
        for r in targets[:12]:
            print(f"  [{r['site_name']}] {r['title'][:58]}")
        return 0

    if not summarizer.check_auth():
        print("LLM 인증 실패 — 중단 (빈 값을 '확인 완료'로 굳히지 않기 위해)")
        return 1

    sites = _site_map()
    lic = reg = none_read = fail = 0
    per_site: Counter[str] = Counter()
    for i, r in enumerate(targets, 1):
        title = r["title"]
        try:
            body, src = _fresh_body(r, sites)
            extracted = summarizer.extract_bid_fields(title, body)
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(targets)}] ✗ {str(exc)[:44]} | {title[:34]}")
            continue

        cols = hub_sync._limit_cols(extracted)
        if not cols:
            none_read += 1
            print(f"  [{i}/{len(targets)}] — 추출 실패, NULL 유지 ({src}) | {title[:34]}")
            continue

        # bids에도 남겨 둔다 — 다음에 또 LLM을 돌리지 않도록
        patch = {k: v for k, v in cols.items()}
        try:
            store.merge_bid_extracted_fields(r["notice_id"], patch)
        except Exception:
            pass

        filled = hub_sync.update_limits(r["notice_id"], extracted)
        lic += "license_limit" in filled
        reg += "region_limit" in filled
        if filled:
            per_site[r["site_name"]] += 1
        L, R = cols.get("license_limit", ""), cols.get("region_limit", "")
        print(f"  [{i}/{len(targets)}] ✅ 면허={L[:40] or '(제한없음)'} | 지역={R[:30] or '(제한없음)'}")
        print(f"        {title[:56]}")

    print(f"\n완료 — license_limit {lic}건 / region_limit {reg}건 채움")
    print(f"       추출 실패(NULL 유지) {none_read}건 / 오류 {fail}건")
    for s, n in per_site.most_common(10):
        print(f"  {s}: {n}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
