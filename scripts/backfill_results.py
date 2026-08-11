"""기존 bids의 결과 공고(지정/선정 결과)에서 선정업체·금액을 뒤늦게 추출해 병합.

본문은 detail 페이지 + 첨부를 다시 받아 쓴다 (선정업체·금액은 대개 첨부 표 안에 있음).
detail 재조회가 실패하면(게재기간 만료로 목록에서 내려간 공고 등) DB의 body_excerpt로 폴백.
extracted_fields는 result 키만 병합 — 기존 7필드는 건드리지 않는다.

사용:
    .venv/bin/python scripts/backfill_results.py --dry-run     # 대상만 출력
    .venv/bin/python scripts/backfill_results.py --limit 3     # 3건만 실제 추출
    .venv/bin/python scripts/backfill_results.py               # 전량
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config

load_config()

from src import attachments as att_mod
from src import store, summarizer
from src.adapters.registry import build_adapter
from src.config import SiteConfig

WORK = Path("/tmp/backfill_results")


def _site_map() -> dict[str, SiteConfig]:
    from src.config import _site_row_to_config

    return {r["name"]: _site_row_to_config(r) for r in store.list_sites()}


def _fresh_body(row: dict[str, Any], sites: dict[str, SiteConfig]) -> tuple[str, str]:
    """detail 재조회 + 첨부 텍스트. 반환 (본문, 출처설명)."""
    site = sites.get(row.get("site_name") or "")
    url = row.get("url") or ""
    fallback = (row.get("body_excerpt") or "").strip()
    if not site or not url:
        return fallback, "body_excerpt(사이트/URL 없음)"

    try:
        adapter = build_adapter(site, store_runtime())
        body, _, _, attachments = adapter._maybe_fetch_detail(url)
    except Exception as exc:
        return fallback, f"body_excerpt(detail 실패: {str(exc)[:40]})"

    if not body and not attachments:
        return fallback, "body_excerpt(detail 내용 없음)"

    texts: list[str] = [body or ""]
    work = WORK / "".join(ch if ch.isalnum() else "_" for ch in row["notice_id"])[:80]
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    n_att = 0
    try:
        for a in (attachments or [])[:5]:
            try:
                src, pdf = att_mod.prepare_for_upload(a.url, a.name, url, work, session=adapter.session)
            except Exception:
                continue
            for f in (pdf, src):
                if f and f.exists():
                    t = att_mod.extract_attachment_text(f)
                    if t and len(t) > 50:
                        texts.append(f"[{a.name}]\n{t}")
                        n_att += 1
                        break
    finally:
        shutil.rmtree(work, ignore_errors=True)

    joined = "\n\n".join(x for x in texts if x).strip()
    if len(joined) < 100 and fallback:
        return fallback, "body_excerpt(재조회 본문 빈약)"
    return joined, f"detail+첨부{n_att}개"


def store_runtime():
    cfg = load_config()
    return cfg.runtime


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="0=전량")
    ap.add_argument("--retry-empty", action="store_true",
                    help="result_kind는 박혔는데 선정업체·금액이 빈 건도 재시도 "
                         "(첫 추출이 본문만 보고 빈손으로 끝난 건 — 여기선 첨부를 다시 받아 본다)")
    args = ap.parse_args()

    rows = store.client().table("bids").select(
        "notice_id, site_name, title, url, body_excerpt, extracted_fields"
    ).limit(2000).execute().data or []
    targets = [r for r in rows if summarizer.is_result_notice(r.get("title") or "")]
    # 이미 값이 찬 건 skip (재실행 안전). --retry-empty면 result_kind만 박히고
    # 값이 빈 건까지 대상에 넣는다 — 그 표시는 '시도했다'는 뜻일 뿐 '없다'는 뜻이 아니다.
    def _empty(ef: dict[str, Any]) -> bool:
        return not ef.get("selected_company") and not ef.get("selected_price")

    todo = [
        r for r in targets
        if _empty(r.get("extracted_fields") or {})
        and (args.retry_empty or not (r.get("extracted_fields") or {}).get("result_kind"))
    ]
    if args.limit:
        todo = todo[: args.limit]

    print(f"결과 공고 {len(targets)}건 / 미추출 {len(todo)}건" + (f" (이번 {len(todo)}건 처리)" if not args.dry_run else ""))
    if args.dry_run:
        for r in todo[:15]:
            print(f"  [{r['site_name']}] {r['title'][:58]}")
        return 0

    ok = miss = fail = 0
    sites = _site_map()
    for i, r in enumerate(todo, 1):
        title = r["title"]
        try:
            body, src_desc = _fresh_body(r, sites)
            result = summarizer.extract_result_fields(title, body)
            if not result:
                fail += 1
                print(f"  [{i}/{len(todo)}] ✗ LLM 실패 | {title[:45]}")
                continue
            store.merge_bid_extracted_fields(r["notice_id"], result)
            comp, price = result.get("selected_company"), result.get("selected_price")
            if comp or price:
                ok += 1
                print(f"  [{i}/{len(todo)}] ✅ {comp or '업체미상'} / "
                      f"{f'{price:,}원' if price else '금액미상'}  ({src_desc}) | {title[:38]}")
            else:
                miss += 1
                print(f"  [{i}/{len(todo)}] — 결과값 없음 ({src_desc}) | {title[:45]}")
        except Exception as exc:
            fail += 1
            print(f"  [{i}/{len(todo)}] ✗ 예외: {str(exc)[:60]} | {title[:40]}")

    print(f"\n완료 — 값 추출 {ok}건 / 값 없음 {miss}건 / 실패 {fail}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
