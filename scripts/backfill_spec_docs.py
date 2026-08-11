"""이미 허브에 들어간 지자체 공고에 공고문 첨부 링크(spec_docs)를 뒤늦게 채운다.

bids에는 첨부 컬럼이 없어서 detail 페이지를 다시 열어 링크만 긁는다.
파일을 내려받지도 LLM을 돌리지도 않으므로 결과공고 백필보다 훨씬 싸다.

url이 목록 주소인 옛 row=N 행은 글 단위로 되돌아갈 수 없어 건너뛴다.

사용:
    .venv/bin/python scripts/backfill_spec_docs.py --dry-run
    .venv/bin/python scripts/backfill_spec_docs.py --limit 5
    .venv/bin/python scripts/backfill_spec_docs.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config

load_config()

from src import hub_sync, store
from src.adapters.registry import build_adapter
from src.config import SiteConfig

# backfill_results와 같은 이유로 옛 이름(-건축/-토목)을 정확 일치 실패 시에만 떼고 찾는다
from backfill_results import _resolve_site, store_runtime  # noqa: E402


def _site_map() -> dict[str, SiteConfig]:
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
            .select("bid_ntce_no, title, spec_docs")
            .eq("source", hub_sync.SOURCE).range(off, off + 999).execute().data or []
        )
        hub += q
        if len(q) < 1000:
            break
    todo_ids = [r["bid_ntce_no"] for r in hub if not r.get("spec_docs")]

    rows = store.client().table("bids").select(
        "notice_id, site_name, title, url"
    ).limit(2000).execute().data or []
    by_id = {r["notice_id"]: r for r in rows}

    targets = [by_id[i] for i in todo_ids if i in by_id]
    # 목록 URL만 있는 행은 특정 글로 되돌아갈 수 없다 (옛 row=N notice_id)
    skipped_list_url = [r for r in targets if "::row=" in r["notice_id"]]
    targets = [r for r in targets if "::row=" not in r["notice_id"]]
    total_with_src = len(targets) + len(skipped_list_url)
    if args.limit:
        targets = targets[: args.limit]

    print(f"허브 local {len(hub)}건 / spec_docs 빈 것 {len(todo_ids)}건")
    print(f"  bids에 원본 있는 것 {total_with_src}건")
    print(f"  목록URL이라 재조회 불가(건너뜀) {len(skipped_list_url)}건")
    print(f"  이번 대상 {len(targets)}건")
    if args.dry_run:
        for r in targets[:15]:
            print(f"  [{r['site_name']}] {r['title'][:56]}")
        return 0

    sites = _site_map()
    ok = zero = fail = 0
    per_site: Counter[str] = Counter()
    for i, r in enumerate(targets, 1):
        site = _resolve_site(r.get("site_name") or "", sites)
        url = r.get("url") or ""
        if not site or not url:
            fail += 1
            continue
        try:
            adapter = build_adapter(site, store_runtime())
            _, _, _, attachments = adapter._maybe_fetch_detail(url)
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(targets)}] ✗ {str(exc)[:50]} | {r['title'][:36]}")
            continue
        docs = hub_sync.build_spec_docs(attachments)
        if not docs:
            zero += 1
            continue
        if hub_sync.update_spec_docs(r["notice_id"], docs):
            ok += 1
            per_site[r["site_name"]] += 1
            print(f"  [{i}/{len(targets)}] ✅ 첨부 {len(docs)}개 | {r['title'][:38]}")
            print(f"        {' / '.join(d['name'][:26] for d in docs[:4])}")
        else:
            fail += 1

    print(f"\n완료 — 채움 {ok}건 / 첨부 0개 {zero}건 / 실패 {fail}건")
    for s, n in per_site.most_common():
        print(f"  {s}: {n}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
