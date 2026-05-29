"""한 사이트의 최근 매칭 글 1건을 강제로 슬랙 발송. 첨부·LLM 모두 정상 흐름 검증용.

사용 예:
    python scripts/send_one_test.py --name "성남시-건축"
    python scripts/send_one_test.py --name "안양시" --keep-db  # DB 마킹까지 수행
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import attachments as att_mod, summarizer
from src.adapters.registry import build_adapter
from src.config import DATA_DIR, load_config
from src.filter import match_keywords
from src.notifier import send_one_posting


def main() -> int:
    parser = argparse.ArgumentParser(description="단일 사이트 최근 매칭 글 1건 강제 발송")
    parser.add_argument("--name", required=True, help="사이트명 (예: 성남시-건축)")
    parser.add_argument("--hours", type=int, default=720, help="며칠치 검색 (시간)")
    parser.add_argument("--index", type=int, default=0, help="매칭 글 중 N번째 (0=가장 최근)")
    parser.add_argument("--no-filter", action="store_true", help="키워드 필터 끄고 첫 글")
    args = parser.parse_args()

    cfg = load_config()
    target = next((s for s in cfg.sites if s.name == args.name), None)
    if target is None:
        print(f"ERROR: '{args.name}' 사이트를 찾을 수 없습니다.")
        return 2

    adapter = build_adapter(target, cfg.runtime)
    adapter.prefilter_titles = cfg.keywords.include
    since = datetime.now() - timedelta(hours=args.hours)
    print(f"[{target.name}] fetching since={since.strftime('%Y-%m-%d %H:%M')} …")
    postings = adapter.fetch(since)
    print(f"   raw count={len(postings)}")

    matched_list: list = []
    for p in postings:
        m = [] if args.no_filter else match_keywords(p, cfg.keywords)
        if m or args.no_filter:
            matched_list.append((p, m))
    if not matched_list:
        print("   매칭되는 글 없음.")
        return 1
    print(f"   매칭={len(matched_list)}건, {args.index}번째 사용")
    if args.index >= len(matched_list):
        print(f"ERROR: index {args.index} >= 매칭 {len(matched_list)}건")
        return 3
    posting, matched = matched_list[args.index]
    print(f"   대상: {posting.title[:60]}")
    print(f"   URL : {posting.url[:120]}")
    print(f"   첨부 anchor: {len(posting.attachments)}개")
    for a in posting.attachments[:8]:
        print(f"     - {a.name[:60]}")

    record = {
        "notice_id": posting.notice_id,
        "site_name": posting.site_name,
        "title": posting.title,
        "org": posting.org,
        "posted_at": posting.posted_at,
        "deadline_at": posting.deadline_at,
        "url": posting.url,
        "estimated_price": posting.estimated_price,
        "region": posting.region,
        "matched_keywords": matched,
        "body": posting.body,
        "category": target.category,
    }

    # 첨부 다운로드 + 변환
    file_paths: list[Path] = []
    attach_texts: list[str] = []
    if posting.attachments:
        work = att_mod.workspace_dir_for(record["notice_id"], DATA_DIR / "attachments")
        for a in posting.attachments[:10]:
            src, pdf = att_mod.prepare_for_upload(
                a.url, a.name, record["url"] or "", work,
                session=adapter.session,
            )
            chosen = pdf if pdf and pdf.exists() else src
            if chosen and chosen.exists():
                file_paths.append(chosen)
            for f in (pdf, src):
                if not f:
                    continue
                text = att_mod.extract_attachment_text(f)
                if text and len(text) > 50:
                    attach_texts.append(f"[{a.name}]\n{text}")
                    break
    print(f"   다운로드된 첨부: {len(file_paths)}개")
    for fp in file_paths:
        print(f"     ✓ {fp.name} ({fp.stat().st_size} bytes)")

    # LLM
    extracted: dict[str, str] = {}
    if summarizer.is_available():
        body_for_llm = record["body"] or ""
        if attach_texts:
            joined = "\n\n".join(attach_texts)
            body_for_llm = body_for_llm + "\n\n[첨부 문서 본문]\n" + joined[:10000]
        try:
            extracted = summarizer.extract_bid_fields(record["title"], body_for_llm)
            non_empty = sum(1 for v in extracted.values() if v)
            print(f"   LLM 추출 {non_empty}/7")
        except Exception as exc:
            print(f"   LLM 실패: {exc}")

    # Slack 발송
    row = dict(record)
    row["extracted_fields"] = extracted
    row["attachments_raw"] = [
        {"name": a.name, "url": a.url} for a in (posting.attachments or [])[:10]
    ]
    ok = send_one_posting(cfg, row, file_paths)
    print(f"   Slack 발송: {'OK' if ok else 'FAIL'}")
    return 0 if ok else 4


if __name__ == "__main__":
    raise SystemExit(main())
