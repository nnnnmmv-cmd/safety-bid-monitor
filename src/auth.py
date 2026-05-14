from __future__ import annotations

from dataclasses import dataclass

import bcrypt

from . import store

ALL_CATEGORIES: list[str] = ["건축", "토목", "건축·토목"]


@dataclass
class User:
    username: str
    name: str
    email: str
    role: str  # "admin" | "viewer"
    categories: list[str]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _row_to_user(row: dict[str, object]) -> User:
    return User(
        username=str(row.get("username") or ""),
        name=str(row.get("name") or row.get("username") or ""),
        email=str(row.get("email") or ""),
        role=str(row.get("role") or "viewer"),
        categories=list(row.get("categories") or []),  # type: ignore[arg-type]
    )


def has_password(username: str) -> bool:
    row = store.get_app_user(username)
    return bool(row and row.get("password_hash"))


def list_users() -> list[User]:
    return [_row_to_user(r) for r in store.list_app_users()]


def authenticate(username: str, password: str) -> User | None:
    row = store.get_app_user(username)
    if not row:
        return None
    if not verify_password(password, str(row.get("password_hash") or "")):
        return None
    return _row_to_user(row)


def upsert_user(
    username: str,
    *,
    password: str | None = None,
    name: str = "",
    email: str = "",
    role: str = "viewer",
    categories: list[str] | None = None,
) -> None:
    existing = store.get_app_user(username) or {}
    record: dict[str, object] = {
        "username": username,
        "password_hash": (
            hash_password(password) if password else str(existing.get("password_hash") or "")
        ),
        "name": name or str(existing.get("name") or username),
        "email": email or str(existing.get("email") or ""),
        "role": role or str(existing.get("role") or "viewer"),
        "categories": categories if categories is not None else list(existing.get("categories") or []),  # type: ignore[arg-type]
    }
    if not record["password_hash"]:
        # 최초 등록인데 비밀번호 안 줬으면 거부
        raise ValueError("새 사용자는 password가 필요합니다.")
    store.upsert_app_user(record)


def delete_user(username: str) -> bool:
    if not store.get_app_user(username):
        return False
    store.delete_app_user(username)
    return True


def user_can_see(user: User, site_category: str) -> bool:
    if user.role == "admin":
        return True
    if not user.categories or not site_category:
        return False
    return site_category in user.categories
