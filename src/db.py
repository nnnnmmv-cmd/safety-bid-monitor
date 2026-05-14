"""DB 호환 레이어 (deprecated).

기존 SQLite 기반 함수들을 Supabase store 호출로 위임. 점진 마이그레이션용.
신규 코드는 `from . import store`로 직접 store를 사용하세요.
"""
from __future__ import annotations

from typing import Any

from . import store


def init_schema(*_args: Any, **_kwargs: Any) -> None:
    """Supabase 사용으로 더 이상 필요 없음 — no-op."""


def insert_if_new(posting: dict[str, Any]) -> bool:
    return store.insert_bid_if_new(posting)


def fetch_unnotified() -> list[dict[str, Any]]:
    return store.fetch_unnotified()


def mark_notified(notice_ids: list[str]) -> None:
    store.mark_notified(notice_ids)
