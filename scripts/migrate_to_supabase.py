"""기존 로컬 데이터(yaml/sqlite)를 Supabase로 일괄 이전 — 한 번만 실행."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from dotenv import load_dotenv

from src import store
from src.config import CONFIG_DIR, DB_PATH, PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


def migrate_sites() -> int:
    path = CONFIG_DIR / "sites.yaml"
    if not path.exists():
        return 0
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites = list(raw.get("sites") or [])
    if not sites:
        return 0
    # 예시-* 항목도 그대로 옮김 — 사용자가 명부에서 enabled=false인 채로 정리 가능
    store.upsert_sites(sites)
    return len(sites)


def migrate_keywords() -> int:
    path = CONFIG_DIR / "keywords.yaml"
    if not path.exists():
        return 0
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    include = list(raw.get("include") or [])
    exclude = list(raw.get("exclude") or [])
    match_in = list(raw.get("require_match_in") or ["title", "body"])
    store.replace_keywords(include, exclude, match_in)
    return len(include) + len(exclude) + len(match_in)


def migrate_users() -> int:
    path = CONFIG_DIR / "users.yaml"
    if not path.exists():
        return 0
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    users = raw.get("users") or {}
    count = 0
    for username, info in users.items():
        if not info or not info.get("password_hash"):
            continue
        store.upsert_app_user({
            "username": username,
            "password_hash": info["password_hash"],
            "name": info.get("name") or username,
            "email": info.get("email") or "",
            "role": info.get("role") or "viewer",
            "categories": list(info.get("categories") or []),
        })
        count += 1
    return count


def migrate_bids() -> int:
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute("SELECT * FROM bids").fetchall())
    finally:
        conn.close()

    inserted = 0
    notified_ids: list[str] = []
    for row in rows:
        d = dict(row)
        try:
            kw = json.loads(d.get("matched_keywords") or "[]")
        except (json.JSONDecodeError, TypeError):
            kw = []
        posting = {
            "notice_id": d["notice_id"],
            "site_name": d["site_name"],
            "title": d["title"],
            "org": d.get("org"),
            "posted_at": d.get("posted_at"),
            "deadline_at": d.get("deadline_at"),
            "url": d.get("url"),
            "estimated_price": d.get("estimated_price"),
            "region": d.get("region") or "",
            "matched_keywords": kw,
            "body": d.get("body_excerpt") or "",
            "category": "",
            "fetched_at": d.get("fetched_at"),
        }
        if store.insert_bid_if_new(posting):
            inserted += 1
        if d.get("notified"):
            notified_ids.append(d["notice_id"])
    if notified_ids:
        store.mark_notified(notified_ids)
    return inserted


def main() -> int:
    if not store.is_configured():
        print("ERROR: SUPABASE 환경변수가 설정되지 않았습니다 (.env 확인)")
        return 2
    print("=== Supabase 마이그레이션 시작 ===")
    print(f"  sites    : {migrate_sites()}건")
    print(f"  keywords : {migrate_keywords()}건")
    print(f"  app_users: {migrate_users()}건")
    print(f"  bids     : {migrate_bids()}건 신규")
    print("=== 완료 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
