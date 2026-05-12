from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .adapters.base import BidPosting
from .config import DB_PATH

SCHEMA: str = """
CREATE TABLE IF NOT EXISTS bids (
    notice_id        TEXT PRIMARY KEY,
    site_name        TEXT NOT NULL,
    title            TEXT NOT NULL,
    org              TEXT,
    posted_at        TEXT,
    deadline_at      TEXT,
    url              TEXT,
    estimated_price  INTEGER,
    region           TEXT,
    matched_keywords TEXT,
    body_excerpt     TEXT,
    fetched_at       TEXT NOT NULL,
    notified         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bids_posted_at  ON bids(posted_at);
CREATE INDEX IF NOT EXISTS idx_bids_deadline   ON bids(deadline_at);
CREATE INDEX IF NOT EXISTS idx_bids_notified   ON bids(notified);
CREATE INDEX IF NOT EXISTS idx_bids_site_name  ON bids(site_name);
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


@contextmanager
def connect(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def insert_if_new(
    conn: sqlite3.Connection,
    posting: BidPosting,
    matched_keywords: list[str],
    fetched_at_iso: str,
) -> bool:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO bids (
            notice_id, site_name, title, org, posted_at, deadline_at,
            url, estimated_price, region, matched_keywords, body_excerpt,
            fetched_at, notified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            posting.notice_id,
            posting.site_name,
            posting.title,
            posting.org,
            _iso(posting.posted_at),
            _iso(posting.deadline_at),
            posting.url,
            posting.estimated_price,
            posting.region,
            json.dumps(matched_keywords, ensure_ascii=False),
            (posting.body or "")[:1000],
            fetched_at_iso,
        ),
    )
    return cur.rowcount == 1


def fetch_unnotified(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT * FROM bids
        WHERE notified = 0
        ORDER BY site_name, posted_at DESC NULLS LAST
        """
    )
    return list(cur.fetchall())


def mark_notified(conn: sqlite3.Connection, notice_ids: list[str]) -> None:
    if not notice_ids:
        return
    placeholders = ",".join("?" * len(notice_ids))
    conn.execute(
        f"UPDATE bids SET notified = 1 WHERE notice_id IN ({placeholders})",
        notice_ids,
    )
