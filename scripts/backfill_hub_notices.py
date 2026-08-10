"""기존 bids를 허브 arch_bid_notices에 source='local'로 일괄 upsert.

이미 슬랙으로 알린 공고들이므로 notified_at을 채워 허브 재알림을 막는다
(notified_at은 bids.fetched_at 사용 — 실제 알림 시각에 가장 가까움).
upsert는 ignore_duplicates라 몇 번 돌려도 안전.

사용:
    .venv/bin/python scripts/backfill_hub_notices.py --dry-run
    .venv/bin/python scripts/backfill_hub_notices.py --limit 5
    .venv/bin/python scripts/backfill_hub_notices.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config

load_config()

from src import hub_sync, store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0=전량")
    args = ap.parse_args()

    rows = store.client().table("bids").select(
        "notice_id, site_name, title, org, posted_at, deadline_at, url, "
        "estimated_price, matched_keywords, extracted_fields, fetched_at"
    ).order("posted_at", desc=True).limit(2000).execute().data or []
    if args.limit:
        rows = rows[: args.limit]

    now = datetime.now(timezone.utc)
    built = [(r, hub_sync.build_row(r, notified_at=r.get("fetched_at"),
                                extracted=r.get("extracted_fields"))) for r in rows]

    rel = Counter(b["relevance"] for _, b in built)
    with_dl = sum(1 for _, b in built if b["bid_clse_dt"])
    future = sum(
        1 for _, b in built
        if b["bid_clse_dt"] and datetime.fromisoformat(b["bid_clse_dt"].replace("Z", "+00:00")) > now
    )
    print(f"대상 {len(built)}건")
    print(f"  relevance: {dict(rel)}")
    print(f"  bid_clse_dt 채워짐: {with_dl}건 (그 중 마감 전 = 허브 목록 노출: {future}건)")
    print(f"  notified_at 채워짐: {sum(1 for _, b in built if b['notified_at'])}건")
    print(f"  presmpt_price(기준금액) 채워짐: {sum(1 for _, b in built if b['presmpt_price'])}건")

    if args.dry_run:
        print("\n샘플 3건:")
        for _, b in built[:3]:
            print(f"  {b['bid_ntce_no']}")
            print(f"    {b['title'][:52]} | {b['institution']} | 마감 {str(b['bid_clse_dt'])[:10]} | {b['relevance']}")
        print("\n--dry-run — 허브에 쓰지 않았습니다.")
        return 0

    ok = fail = award = base = 0
    for r, row in built:
        if hub_sync.upsert_bid(r, notified_at=r.get("fetched_at"),
                               extracted=r.get("extracted_fields")):
            ok += 1
            # upsert는 ignore_duplicates라 기존 행을 안 건드린다 —
            # 뒤늦게 확보한 기준금액은 update로 따로 채운다
            if row["presmpt_price"] and hub_sync.update_base_price(r["notice_id"], row["presmpt_price"]):
                base += 1
            ef = r.get("extracted_fields") or {}
            if ef.get("selected_company") or ef.get("selected_price"):
                if hub_sync.upsert_award(r["notice_id"], ef.get("selected_company"), ef.get("selected_price")):
                    award += 1
        else:
            fail += 1
    print(f"\n완료 — upsert {ok}건 / 실패 {fail}건 / 기준금액 반영 {base}건 / award 반영 {award}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
