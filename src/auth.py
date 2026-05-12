from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
import yaml

from .config import CONFIG_DIR

USERS_PATH: Path = CONFIG_DIR / "users.yaml"
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


def _load_raw() -> dict[str, Any]:
    if not USERS_PATH.exists():
        return {"users": {}}
    return yaml.safe_load(USERS_PATH.read_text(encoding="utf-8")) or {"users": {}}


def _save_raw(data: dict[str, Any]) -> None:
    USERS_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )


def has_password(username: str) -> bool:
    raw = _load_raw()
    info = (raw.get("users") or {}).get(username)
    if not info:
        return False
    return bool(info.get("password_hash"))


def list_users() -> list[User]:
    raw = _load_raw()
    return [
        User(
            username=uname,
            name=str(info.get("name") or uname),
            email=str(info.get("email") or ""),
            role=str(info.get("role") or "viewer"),
            categories=list(info.get("categories") or []),
        )
        for uname, info in (raw.get("users") or {}).items()
    ]


def authenticate(username: str, password: str) -> User | None:
    raw = _load_raw()
    info = (raw.get("users") or {}).get(username)
    if not info:
        return None
    if not verify_password(password, str(info.get("password_hash") or "")):
        return None
    return User(
        username=username,
        name=str(info.get("name") or username),
        email=str(info.get("email") or ""),
        role=str(info.get("role") or "viewer"),
        categories=list(info.get("categories") or []),
    )


def upsert_user(
    username: str,
    *,
    password: str | None = None,
    name: str = "",
    email: str = "",
    role: str = "viewer",
    categories: list[str] | None = None,
) -> None:
    raw = _load_raw()
    users = raw.setdefault("users", {})
    existing = users.get(username) or {}
    if password:
        existing["password_hash"] = hash_password(password)
    if name:
        existing["name"] = name
    if email:
        existing["email"] = email
    if role:
        existing["role"] = role
    if categories is not None:
        existing["categories"] = categories
    users[username] = existing
    _save_raw(raw)


def delete_user(username: str) -> bool:
    raw = _load_raw()
    users = raw.get("users") or {}
    if username not in users:
        return False
    users.pop(username)
    raw["users"] = users
    _save_raw(raw)
    return True


def user_can_see(user: User, site_category: str) -> bool:
    """사이트의 category가 user.categories에 포함되면 표시."""
    if user.role == "admin":
        return True
    if not user.categories:
        return False
    if not site_category:
        return False
    return site_category in user.categories
