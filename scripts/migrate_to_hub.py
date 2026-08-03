"""옛 Supabase(크롤러 전용) → 허브 Supabase 1회성 데이터 이관.

- 대상 테이블: bids / sites / keywords / app_users
- 1,000행 단위 페이지네이션 (Supabase 기본 응답 상한)
- 완료 후 테이블별 행수 옛=새 대조, 불일치 시 비정상 종료

사용:
    .venv/bin/python scripts/migrate_to_hub.py            # 실제 이관
    .venv/bin/python scripts/migrate_to_hub.py --dry-run  # 읽기만
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import dotenv_values
from supabase import create_client

from src.config import PROJECT_ROOT

# 이관 순서 — 참조 무결성 제약은 없지만 명부(sites)를 먼저 넣어두는 편이 읽기 편함
TABLES: list[tuple[str, str]] = [
    ("sites", "name"),
    ("keywords", "id"),
    ("app_users", "username"),
    ("bids", "notice_id"),
]
PAGE = 1000
HUB_ENV = Path.home() / "homecheck-sales-hub" / ".env.local"


def _client(url: str, key: str):
    if not (url and key):
        raise SystemExit("접속값 누락 — .env / .env.local 확인")
    return create_client(url, key)


def fetch_all(client, table: str, order_col: str) -> list[dict[str, Any]]:
    """1,000행씩 끊어서 전량 조회."""
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        res = client.table(table).select("*").order(order_col).range(start, start + PAGE - 1).execute()
        chunk = res.data or []
        rows.extend(chunk)
        if len(chunk) < PAGE:
            return rows
        start += PAGE


def count_of(client, table: str) -> int:
    return client.table(table).select("*", count="exact").limit(1).execute().count or 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="읽기만 하고 새 DB에 쓰지 않음")
    args = ap.parse_args()

    old_env = dotenv_values(PROJECT_ROOT / ".env")
    old = _client(
        (old_env.get("SUPABASE_URL") or "").strip(),
        (old_env.get("SUPABASE_SERVICE_KEY") or old_env.get("SUPABASE_ANON_KEY") or "").strip(),
    )
    hub_env = dotenv_values(HUB_ENV)
    new = _client(
        (hub_env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip(),
        (hub_env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip(),
    )

    print(f"{'테이블':12} {'옛':>6} {'복사':>6} {'새':>6}  결과")
    print("-" * 44)
    mismatched: list[str] = []

    for table, order_col in TABLES:
        src_rows = fetch_all(old, table, order_col)
        src_n = len(src_rows)

        copied = 0
        if not args.dry_run and src_rows:
            # sites/keywords의 id는 identity 컬럼 — 옛 값을 그대로 넣어 참조 흔들림 방지
            for i in range(0, src_n, PAGE):
                batch = src_rows[i : i + PAGE]
                new.table(table).upsert(batch, on_conflict=order_col).execute()
                copied += len(batch)

        dst_n = count_of(new, table)
        ok = args.dry_run or (dst_n == src_n)
        if not ok:
            mismatched.append(table)
        print(f"{table:12} {src_n:>6} {copied:>6} {dst_n:>6}  {'OK' if ok else '❌ 불일치'}")

    if args.dry_run:
        print("\n--dry-run — 새 DB에 쓰지 않았습니다.")
        return 0
    if mismatched:
        print(f"\n❌ 행수 불일치: {', '.join(mismatched)} — 이관 중단, 원인 확인 필요")
        return 1
    print("\n✅ 4개 테이블 전부 옛=새 행수 일치")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
