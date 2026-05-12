from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from .adapters.registry import build_adapter
from .config import LOG_DIR, AppConfig, SiteConfig, load_config
from .db import connect, fetch_unnotified, init_schema, insert_if_new, mark_notified
from .filter import match_keywords
from .notifier import notify_error, notify_new_postings
from .utils import utc_now_iso

logger: logging.Logger = logging.getLogger("safetybid")


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "monitor.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, console])


def _process_site(cfg: AppConfig, site: SiteConfig, since: datetime) -> tuple[int, int, str | None]:
    """returns (fetched, inserted, error_message)"""
    try:
        adapter = build_adapter(site, cfg.runtime)
        postings = adapter.fetch(since)
    except Exception as exc:
        logger.exception("[%s] adapter failed", site.name)
        return 0, 0, f"{site.name}: {exc}"

    inserted = 0
    fetched_at = utc_now_iso()
    with connect() as conn:
        for posting in postings:
            matched = match_keywords(posting, cfg.keywords)
            if not matched:
                continue
            if insert_if_new(conn, posting, matched, fetched_at):
                inserted += 1
    logger.info("[%s] fetched=%d inserted=%d", site.name, len(postings), inserted)
    return len(postings), inserted, None


def run_once() -> None:
    _setup_logging()
    cfg = load_config()
    init_schema()

    if not cfg.sites:
        logger.warning("활성화된 사이트가 없습니다. config/sites.yaml에서 enabled=true 항목을 추가하세요.")
        return

    since = datetime.now() - timedelta(hours=cfg.runtime.lookback_hours)
    errors: list[str] = []
    total_fetched = 0
    total_inserted = 0

    for site in cfg.sites:
        fetched, inserted, err = _process_site(cfg, site, since)
        total_fetched += fetched
        total_inserted += inserted
        if err:
            errors.append(err)

    with connect() as conn:
        new_rows = [dict(r) for r in fetch_unnotified(conn)]
    # 사이트 메타데이터(구분 등)를 알림용으로 주입
    site_meta = {s.name: s for s in cfg.sites}
    for r in new_rows:
        site = site_meta.get(str(r.get("site_name") or ""))
        r["category"] = site.category if site else ""
    logger.info("총 fetched=%d inserted=%d unnotified=%d", total_fetched, total_inserted, len(new_rows))

    if new_rows:
        try:
            notify_new_postings(cfg, new_rows)
            with connect() as conn:
                mark_notified(conn, [str(r["notice_id"]) for r in new_rows])
        except Exception as exc:
            logger.exception("알림 발송 실패")
            errors.append(f"notify: {exc}")

    if errors:
        try:
            notify_error(
                cfg,
                summary=f"모니터 실행 중 {len(errors)}건 오류",
                detail="\n".join(errors),
            )
        except Exception:
            logger.exception("관리자 알림 발송도 실패")


def main() -> int:
    try:
        run_once()
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
