from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_DIR: Path = PROJECT_ROOT / "config"
DATA_DIR: Path = PROJECT_ROOT / "data"
LOG_DIR: Path = PROJECT_ROOT / "logs"
SNAPSHOT_DIR: Path = DATA_DIR / "snapshots"
DB_PATH: Path = DATA_DIR / "bids.db"


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
    webhook_url: str
    admin_webhook_url: str


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
    # 영업 메타데이터 — 크롤링 동작과는 무관, 알림 태그·대시보드 명부에 사용
    category: str = ""  # "건축" / "토목" / "건축·토목"
    last_updated: str = ""  # 명부 정보 갱신일 (YYYY-MM-DD)
    homecheck: str = ""  # "O" / "X"
    hansijin: str = ""
    hanjugum: str = ""
    bidding_status: str = ""  # "진행" / "불가" / "보류"
    new_submission_date: str = ""
    period_start: str = ""
    period_end: str = ""
    announce_planned_date: str = ""
    previous_announce_date: str = ""
    previous_deadline: str = ""
    under_100m_winner_method: str = ""
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
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return None
    return SlackConfig(
        webhook_url=webhook,
        admin_webhook_url=os.getenv("SLACK_ADMIN_WEBHOOK_URL", "").strip() or webhook,
    )


def load_config() -> AppConfig:
    load_dotenv(PROJECT_ROOT / ".env")

    smtp = _load_smtp()
    slack = _load_slack()
    runtime = RuntimeConfig(
        lookback_hours=int(os.getenv("LOOKBACK_HOURS", "48")),
        request_delay_sec=float(os.getenv("REQUEST_DELAY_SEC", "1.0")),
        http_timeout_sec=float(os.getenv("HTTP_TIMEOUT_SEC", "15")),
    )

    sites_raw: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "sites.yaml").read_text(encoding="utf-8")) or {}
    sites: list[SiteConfig] = []
    for entry in sites_raw.get("sites", []) or []:
        if not entry.get("enabled", False):
            continue
        sites.append(
            SiteConfig(
                name=entry["name"],
                adapter=entry["adapter"],
                base_url=str(entry.get("base_url", "")).rstrip("/"),
                list_url=str(entry.get("list_url", "")),
                list_params={str(k): str(v) for k, v in (entry.get("list_params") or {}).items()},
                selectors=dict(entry.get("selectors") or {}),
                pagination=dict(entry.get("pagination") or {}),
                region=entry.get("region", ""),
                enabled=True,
                category=str(entry.get("category", "") or ""),
                last_updated=str(entry.get("last_updated", "") or ""),
                homecheck=str(entry.get("homecheck", "") or ""),
                hansijin=str(entry.get("hansijin", "") or ""),
                hanjugum=str(entry.get("hanjugum", "") or ""),
                bidding_status=str(entry.get("bidding_status", "") or ""),
                new_submission_date=str(entry.get("new_submission_date", "") or ""),
                period_start=str(entry.get("period_start", "") or ""),
                period_end=str(entry.get("period_end", "") or ""),
                announce_planned_date=str(entry.get("announce_planned_date", "") or ""),
                previous_announce_date=str(entry.get("previous_announce_date", "") or ""),
                previous_deadline=str(entry.get("previous_deadline", "") or ""),
                under_100m_winner_method=str(entry.get("under_100m_winner_method", "") or ""),
                note=str(entry.get("note", "") or ""),
            )
        )

    kw_raw: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "keywords.yaml").read_text(encoding="utf-8")) or {}
    keywords = KeywordConfig(
        include=list(kw_raw.get("include") or []),
        exclude=list(kw_raw.get("exclude") or []),
        require_match_in=list(kw_raw.get("require_match_in") or ["title"]),
    )

    return AppConfig(smtp=smtp, slack=slack, runtime=runtime, sites=sites, keywords=keywords)
