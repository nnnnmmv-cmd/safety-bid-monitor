from __future__ import annotations

from ..config import RuntimeConfig, SiteConfig
from .base import Adapter
from .egov import EgovAdapter
from .eminwon import EminwonAdapter
from .playwright_adapter import PlaywrightAdapter

_REGISTRY: dict[str, type[Adapter]] = {
    "egov": EgovAdapter,
    "eminwon": EminwonAdapter,
    "playwright": PlaywrightAdapter,
}


def build_adapter(site: SiteConfig, runtime: RuntimeConfig) -> Adapter:
    cls = _REGISTRY.get(site.adapter)
    if cls is None:
        raise ValueError(f"Unknown adapter '{site.adapter}' for site '{site.name}'")
    return cls(site, runtime)
