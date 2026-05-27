from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests

from ..config import RuntimeConfig, SiteConfig


@dataclass
class Attachment:
    name: str           # 사람이 보는 파일명 (예: "공고문.hwp")
    url: str            # 직접 다운로드 가능한 절대 URL
    sys_name: str = ""  # 서버측 저장 파일명 (수원시처럼 별개일 때)


@dataclass
class BidPosting:
    notice_id: str
    site_name: str
    title: str
    org: str
    posted_at: datetime | None
    deadline_at: datetime | None
    url: str
    estimated_price: int | None
    body: str
    raw_html: str
    region: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    attachments: list[Attachment] = field(default_factory=list)


import ssl
import urllib3
from urllib3 import PoolManager
from requests.adapters import HTTPAdapter

# 한국 정부 사이트 인증서 체인 누락이 많아 verify=False로 쓰는데, 그 경고 노이즈 차단.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class _LegacyHTTPSAdapter(HTTPAdapter):
    """오래된 SSL/TLS만 지원하는 한국 정부 사이트 호환용.

    1) cipher SECLEVEL 완화 — 안양·용인 등 SSLV3_ALERT_HANDSHAKE_FAILURE 대응
    2) OP_LEGACY_SERVER_CONNECT — TLS 1.2 미만 사이트
    3) 인증서 체인 검증 끔 — 성남·의정부·이천·평택도시공사 CERTIFICATE_VERIFY_FAILED 대응
       (전부 공공 도메인이라 MITM 위험은 실무상 낮음)
    """

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs):  # type: ignore[no-untyped-def]
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        try:
            ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        except Exception:
            pass
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize, block=block,
            ssl_context=ctx, **pool_kwargs,
        )


class Adapter(ABC):
    def __init__(self, site: SiteConfig, runtime: RuntimeConfig) -> None:
        self.site = site
        self.runtime = runtime
        # monitor가 주입하는 사전 매칭용 키워드 — title 매칭 안 되면 detail fetch 스킵
        self.prefilter_titles: list[str] = []
        self.session = requests.Session()
        # legacy SSL 한국 정부 사이트 대응 — cipher/verify 완화
        self.session.mount("https://", _LegacyHTTPSAdapter())
        self.session.verify = False
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36 SafetyBidMonitor/0.1"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
            }
        )

    @abstractmethod
    def fetch(self, since: datetime) -> list[BidPosting]:
        """`since` 이후 게시된 공고 리스트를 반환."""

    def _get(self, url: str, params: dict[str, str] | None = None, retries: int = 3) -> str:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                resp = self.session.get(
                    url,
                    params=params or {},
                    timeout=self.runtime.http_timeout_sec,
                )
                resp.raise_for_status()
                resp.encoding = resp.apparent_encoding or resp.encoding
                time.sleep(self.runtime.request_delay_sec)
                return resp.text
            except (requests.RequestException, requests.Timeout) as exc:
                last_err = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"GET {url} failed after {retries} retries: {last_err}")

    def _make_notice_id(self, raw_id: str) -> str:
        if raw_id:
            return f"{self.site.name}::{raw_id}"
        digest = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:16]
        return f"{self.site.name}::hash::{digest}"
