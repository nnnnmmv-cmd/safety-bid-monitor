"""한 사이트만 골라 어댑터 동작을 확인한다.

사용 예:
    python scripts/test_site.py --name "예시-행정안전부"
    python scripts/test_site.py --name "예시-행정안전부" --hours 168 --limit 20
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters.registry import build_adapter
from src.config import load_config
from src.filter import match_keywords


def main() -> int:
    parser = argparse.ArgumentParser(description="단일 사이트 어댑터 동작 확인")
    parser.add_argument("--name", required=True, help="sites.yaml의 name과 동일")
    parser.add_argument("--hours", type=int, default=168, help="며칠치를 볼지 (시간 단위, 기본 168=7일)")
    parser.add_argument("--limit", type=int, default=10, help="콘솔 출력 최대 건수")
    parser.add_argument("--no-filter", action="store_true", help="키워드 필터 끄고 전부 출력")
    args = parser.parse_args()

    cfg = load_config()
    target = next((s for s in cfg.sites if s.name == args.name), None)
    if target is None:
        # enabled=false인 사이트도 테스트할 수 있게 yaml에서 다시 찾기
        import yaml
        from src.config import CONFIG_DIR, SiteConfig
        raw = yaml.safe_load((CONFIG_DIR / "sites.yaml").read_text(encoding="utf-8")) or {}
        for entry in raw.get("sites", []) or []:
            if entry.get("name") == args.name:
                target = SiteConfig(
                    name=entry["name"],
                    adapter=entry["adapter"],
                    base_url=entry["base_url"].rstrip("/"),
                    list_url=entry["list_url"],
                    list_params={str(k): str(v) for k, v in (entry.get("list_params") or {}).items()},
                    selectors=dict(entry.get("selectors") or {}),
                    pagination=dict(entry.get("pagination") or {}),
                    region=entry.get("region", ""),
                    enabled=True,
                )
                break
        if target is None:
            print(f"ERROR: '{args.name}' 사이트를 sites.yaml에서 찾을 수 없습니다.")
            return 2

    adapter = build_adapter(target, cfg.runtime)
    since = datetime.now() - timedelta(hours=args.hours)
    print(f"[{target.name}] since={since.isoformat(timespec='minutes')} adapter={target.adapter}")
    print(f"   list_url={target.list_url}")
    postings = adapter.fetch(since)
    print(f"   raw count={len(postings)}")

    shown = 0
    for posting in postings:
        matched = [] if args.no_filter else match_keywords(posting, cfg.keywords)
        if not args.no_filter and not matched:
            continue
        shown += 1
        if shown > args.limit:
            break
        date_text = posting.posted_at.strftime("%Y-%m-%d") if posting.posted_at else "----"
        kw_text = ", ".join(matched) if matched else "(no filter)"
        print(f"  - [{date_text}] {posting.title}")
        print(f"      keywords: {kw_text}")
        print(f"      url: {posting.url}")
    print(f"   shown={shown} (limit={args.limit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
