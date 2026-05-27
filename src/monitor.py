from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from pathlib import Path

from . import attachments as att_mod
from . import store, summarizer
from .adapters.registry import build_adapter
from .config import DATA_DIR, LOG_DIR, AppConfig, SiteConfig, load_config
from .filter import match_keywords
from .notifier import notify_error, notify_new_postings, send_one_posting
from .utils import utc_now_iso

logger: logging.Logger = logging.getLogger("safetybid")

# 사이트별 추정가 상한 — 이 금액 이상은 발송에서 제외.
# 안양시·과천시는 1억 이상 용역이 해당 시 소재 업체만 입찰 가능해서 자사에 무의미.
# estimated_price=None(=가격 미파싱) 글은 통과 — 사용자가 확인.
SITE_PRICE_CAP: dict[str, int] = {
    "안양시": 100_000_000,
    "과천시": 100_000_000,
}


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "monitor.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, console])


# run_once에서 set — _process_site가 참조. LLM 인증 실패 시 호출 skip해서
# 60+회 무의미한 401 시도를 막고, 본 흐름(첨부·발송)은 정상 진행.
_LLM_AUTH_OK: bool = True


def _process_site(cfg: AppConfig, site: SiteConfig, since: datetime) -> tuple[int, int, str | None]:
    """returns (fetched, inserted, error_message). 한 사이트 실패가 전체 run을 망치지 않도록 전체를 격리."""
    try:
        adapter = build_adapter(site, cfg.runtime)
        adapter.prefilter_titles = cfg.keywords.include  # detail fetch 절감용
        postings = adapter.fetch(since)
    except Exception as exc:
        logger.exception("[%s] adapter failed", site.name)
        return 0, 0, f"{site.name} fetch: {exc}"

    inserted = 0
    fetched_at = utc_now_iso()
    insert_errors = 0
    for posting in postings:
        try:
            matched = match_keywords(posting, cfg.keywords)
            if not matched:
                continue
            # 사이트별 추정가 상한 적용 (안양시·과천시 1억 이상 제외)
            cap = SITE_PRICE_CAP.get(site.name)
            if cap and posting.estimated_price and posting.estimated_price >= cap:
                logger.info(
                    "[%s] skip(>=%d만원): %s (%d원)",
                    site.name, cap // 10_000, posting.title[:30], posting.estimated_price,
                )
                continue
            record = {
                "notice_id": posting.notice_id,
                "site_name": posting.site_name,
                "title": posting.title,
                "org": posting.org,
                "posted_at": posting.posted_at,
                "deadline_at": posting.deadline_at,
                "url": posting.url,
                "estimated_price": posting.estimated_price,
                "region": posting.region,
                "matched_keywords": matched,
                "body": posting.body,
                "category": site.category,
                "fetched_at": fetched_at,
            }
            if store.insert_bid_if_new(record):
                inserted += 1
                # 1) 첨부파일 다운로드 + HWP→PDF 변환 (pyhwp + reportlab)
                file_paths: list[Path] = []
                attach_texts: list[str] = []
                if posting.attachments:
                    work = att_mod.workspace_dir_for(record["notice_id"], DATA_DIR / "attachments")
                    for a in posting.attachments[:10]:
                        # 한 첨부 실패가 전체를 망치지 않게 격리 — 본 흐름(LLM·발송) 보장
                        try:
                            src, pdf = att_mod.prepare_for_upload(a.url, a.name, record["url"] or "", work)
                        except Exception as att_exc:
                            logger.warning("[%s] 첨부 다운로드 실패 (%s): %s", site.name, a.name[:40], att_exc)
                            continue
                        # PDF가 있으면 PDF만 첨부 (HWP→PDF 변환 결과 또는 원본 PDF), 없으면 원본
                        chosen = pdf if pdf and pdf.exists() else src
                        if chosen and chosen.exists():
                            file_paths.append(chosen)
                        # 텍스트 추출 — LLM 본문 보강용 (PDF 또는 HWP 중 하나)
                        for f in (pdf, src):
                            if not f:
                                continue
                            text = att_mod.extract_attachment_text(f)
                            if text and len(text) > 50:
                                attach_texts.append(f"[{a.name}]\n{text}")
                                break

                # 2) LLM 7개 필드 추출 (detail 본문 + 모든 첨부 본문 합쳐서)
                # 인증 만료(_LLM_AUTH_OK=False) 시 건너뜀 — 60+회 401 시도 방지
                extracted: dict[str, str] = {}
                if _LLM_AUTH_OK and summarizer.is_available():
                    try:
                        body_for_llm = record["body"] or ""
                        if attach_texts:
                            joined = "\n\n".join(attach_texts)
                            body_for_llm = body_for_llm + "\n\n[첨부 문서 본문]\n" + joined[:10000]
                        extracted = summarizer.extract_bid_fields(record["title"], body_for_llm)
                        non_empty = sum(1 for v in extracted.values() if v)
                        logger.info("[%s] LLM 추출 %d/7 (%s)", site.name, non_empty, record["title"][:30])
                        if any(extracted.values()):
                            store.update_bid_extracted_fields(record["notice_id"], extracted)
                    except Exception as ex:
                        logger.warning("[%s] LLM 요약 실패 (%s): %s", site.name, record["notice_id"], ex)

                # 3) Slack 즉시 발송 (한 공고 = 한 메시지 + thread에 첨부)
                row_for_send = dict(record)
                row_for_send["extracted_fields"] = extracted
                # 슬랙 채널 attach 실패 케이스 대비 — 본문에 원본 다운로드 URL 함께 노출
                row_for_send["attachments_raw"] = [
                    {"name": a.name, "url": a.url} for a in (posting.attachments or [])[:10]
                ]
                if send_one_posting(cfg, row_for_send, file_paths):
                    store.mark_notified([record["notice_id"]])
                    logger.info("[%s] 발송 완료: %s (첨부 %d개)", site.name, record["title"][:30], len(file_paths))
                else:
                    logger.warning("[%s] 발송 실패: %s", site.name, record["title"][:30])
        except Exception as exc:
            insert_errors += 1
            logger.warning(
                "[%s] insert 실패 (notice_id=%s): %s",
                site.name, getattr(posting, "notice_id", "?"), exc,
            )
            logger.debug("[%s] traceback:", site.name, exc_info=True)
    if insert_errors:
        logger.info("[%s] fetched=%d inserted=%d insert_errors=%d", site.name, len(postings), inserted, insert_errors)
    else:
        logger.info("[%s] fetched=%d inserted=%d", site.name, len(postings), inserted)
    return len(postings), inserted, None


def run_once() -> None:
    _setup_logging()
    cfg = load_config()

    if not cfg.sites:
        logger.warning("활성화된 사이트가 없습니다. 대시보드의 발주청 명부에서 모니터링을 체크하세요.")
        return

    # cron 시작 시 LLM 인증 사전 체크 — 만료면 LLM 호출 skip + admin 알림
    global _LLM_AUTH_OK
    ok, reason = summarizer.check_auth()
    _LLM_AUTH_OK = ok
    if not ok:
        logger.warning("LLM 인증 실패 — 이번 cron에서 LLM 추출 skip: %s", reason)
        try:
            notify_error(
                cfg,
                summary="🔑 Claude Max OAuth 만료 — LLM 추출 일시 중단",
                detail=(
                    f"증상: {reason}\n\n"
                    "조치 방법:\n"
                    "1) 터미널에서 `claude` 실행\n"
                    "2) Claude Code 프롬프트에서 `/login` 입력\n"
                    "3) 브라우저 로그인 + 인증 코드 입력\n"
                    "4) `launchctl kickstart -k gui/$(id -u)/com.openclaw.claude-max-proxy`\n\n"
                    "이번 cron은 LLM 없이 진행 (첨부·슬랙 발송은 정상)."
                ),
            )
        except Exception:
            logger.exception("admin 알림 발송 실패")

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

    # 새 흐름: 각 사이트 INSERT 직후 즉시 발송하므로 여기 unnotified는 대부분 비어 있음.
    # 다만 이전 실행에서 발송 실패 등으로 남은 미발송이 있다면 첨부 없이 fallback 발송.
    new_rows = store.fetch_unnotified()
    site_meta = {s.name: s for s in cfg.sites}
    for r in new_rows:
        site = site_meta.get(str(r.get("site_name") or ""))
        r["category"] = site.category if site else r.get("category", "")
    logger.info(
        "총 fetched=%d inserted=%d 이전 미발송 fallback=%d",
        total_fetched, total_inserted, len(new_rows),
    )

    if new_rows:
        try:
            notify_new_postings(cfg, new_rows)
            store.mark_notified([str(r["notice_id"]) for r in new_rows])
        except Exception as exc:
            logger.exception("fallback 알림 발송 실패")
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
