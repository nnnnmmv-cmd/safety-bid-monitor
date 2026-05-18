from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = PROJECT_ROOT / "config"
DATA_DIR: Path = PROJECT_ROOT / "data"
LOG_DIR: Path = PROJECT_ROOT / "logs"
SNAPSHOT_DIR: Path = DATA_DIR / "snapshots"
DB_PATH: Path = DATA_DIR / "bids.db"  # 마이그레이션 후 사용 안 함 (백업용 보존)


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    app_password: str
    notify_to: list[str]
    notify_admin: str


@dataclass
class SlackConfig:
    webhook_url: str           # 카테고리 없는 공고용 fallback (선택)
    admin_webhook_url: str     # 에러 알림용 (선택)
    building_webhook_url: str  # 건축 카테고리 채널
    civil_webhook_url: str     # 토목 카테고리 채널


@dataclass
class RuntimeConfig:
    lookback_hours: int
    request_delay_sec: float
    http_timeout_sec: float


@dataclass
class SiteConfig:
    name: str
    adapter: str
    base_url: str
    list_url: str
    list_params: dict[str, str] = field(default_factory=dict)
    selectors: dict[str, str] = field(default_factory=dict)
    pagination: dict[str, Any] = field(default_factory=dict)
    region: str = ""
    enabled: bool = True
    category: str = ""
    last_updated: str = ""
    homecheck: str = ""
    hansijin: str = ""
    hanjugum: str = ""
    bidding_status: str = ""
    new_submission_date: str = ""
    period_start: str = ""
    period_end: str = ""
    announce_planned_date: str = ""
    previous_announce_date: str = ""
    previous_deadline: str = ""
    under_100m_winner_method: str = ""
    above_100m_winner_method: str = ""
    bid_submission_method: str = ""
    performance_proof: str = ""
    work_overlap_doc: str = ""
    note: str = ""


@dataclass
class KeywordConfig:
    include: list[str]
    exclude: list[str]
    require_match_in: list[str]


@dataclass
class AppConfig:
    smtp: SmtpConfig | None
    slack: SlackConfig | None
    runtime: RuntimeConfig
    sites: list[SiteConfig]
    keywords: KeywordConfig


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_smtp() -> SmtpConfig | None:
    user = os.getenv("SMTP_USER", "").strip()
    pw = os.getenv("SMTP_APP_PASSWORD", "").strip()
    notify_to = _split_csv(os.getenv("NOTIFY_TO", ""))
    if not (user and pw and notify_to):
        return None
    return SmtpConfig(
        host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
        port=int(os.getenv("SMTP_PORT", "587")),
        user=user,
        app_password=pw,
        notify_to=notify_to,
        notify_admin=os.getenv("NOTIFY_ADMIN", "").strip(),
    )


def _load_slack() -> SlackConfig | None:
    fallback = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    building = os.getenv("SLACK_WEBHOOK_BUILDING", "").strip()
    civil = os.getenv("SLACK_WEBHOOK_CIVIL", "").strip()
    admin = os.getenv("SLACK_ADMIN_WEBHOOK_URL", "").strip()
    # 하나라도 있으면 활성화
    if not (fallback or building or civil or admin):
        return None
    return SlackConfig(
        webhook_url=fallback,
        admin_webhook_url=admin or fallback or building or civil,
        building_webhook_url=building,
        civil_webhook_url=civil,
    )


def _site_row_to_config(row: dict[str, Any]) -> SiteConfig:
    return SiteConfig(
        name=str(row.get("name") or ""),
        adapter=str(row.get("adapter") or "egov"),
        base_url=str(row.get("base_url") or "").rstrip("/"),
        list_url=str(row.get("list_url") or ""),
        list_params={str(k): str(v) for k, v in (row.get("list_params") or {}).items()},
        selectors=dict(row.get("selectors") or {}),
        pagination=dict(row.get("pagination") or {}),
        region=str(row.get("region") or ""),
        enabled=bool(row.get("enabled")),
        category=str(row.get("category") or ""),
        last_updated=str(row.get("last_updated") or ""),
        homecheck=str(row.get("homecheck") or ""),
        hansijin=str(row.get("hansijin") or ""),
        hanjugum=str(row.get("hanjugum") or ""),
        bidding_status=str(row.get("bidding_status") or ""),
        new_submission_date=str(row.get("new_submission_date") or ""),
        period_start=str(row.get("period_start") or ""),
        period_end=str(row.get("period_end") or ""),
        announce_planned_date=str(row.get("announce_planned_date") or ""),
        previous_announce_date=str(row.get("previous_announce_date") or ""),
        previous_deadline=str(row.get("previous_deadline") or ""),
        under_100m_winner_method=str(row.get("under_100m_winner_method") or ""),
        above_100m_winner_method=str(row.get("above_100m_winner_method") or ""),
        bid_submission_method=str(row.get("bid_submission_method") or ""),
        performance_proof=str(row.get("performance_proof") or ""),
        work_overlap_doc=str(row.get("work_overlap_doc") or ""),
        note=str(row.get("note") or ""),
    )


def load_config() -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env")

    # 순환 import 방지 — 함수 안에서 import
    from . import store

    smtp = _load_smtp()
    slack = _load_slack()
    runtime = RuntimeConfig(
        lookback_hours=int(os.getenv("LOOKBACK_HOURS", "48")),
        request_delay_sec=float(os.getenv("REQUEST_DELAY_SEC", "1.0")),
        http_timeout_sec=float(os.getenv("HTTP_TIMEOUT_SEC", "15")),
    )

    site_rows = store.list_sites()
    sites: list[SiteConfig] = [
        _site_row_to_config(row) for row in site_rows if row.get("enabled")
    ]

    kw = store.list_keywords()
    keywords = KeywordConfig(
        include=kw.get("include", []),
        exclude=kw.get("exclude", []),
        require_match_in=kw.get("match_in", ["title", "body"]) or ["title", "body"],
    )

    return AppConfig(smtp=smtp, slack=slack, runtime=runtime, sites=sites, keywords=keywords)
