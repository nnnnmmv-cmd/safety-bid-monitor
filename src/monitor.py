from __future__ import annotations

import logging
import sys
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from pathlib import Path

from . import attachments as att_mod
from . import hub_sync, store, summarizer
from .adapters.registry import build_adapter
from .config import DATA_DIR, LOG_DIR, AppConfig, SiteConfig, load_config
from .filter import match_keywords
from .notifier import (
    _classify_post_category,
    notify_error,
    notify_new_postings,
    send_one_posting,
    should_skip_arch_notify,
)
from .utils import utc_now_iso

logger: logging.Logger = logging.getLogger("safetybid")

# 금액 상한 필터 제거(2026-08-10): 안양시는 명부가 금액으로 갈리는데
# '나'(1억 이상·경기도 업체)에 홈체크·한시진이 등록돼 있어 1억 이상이 오히려 투찰 대상이다.
# 명부 규칙은 지자체마다 연 1회 갱신되므로 크롤러가 금액으로 거르면 규칙이 바뀔 때마다
# 크롤러도 고쳐야 하고 그 사이 공고는 영영 못 본다.
# → 수집은 넓게, 참가 가능 판정은 허브 한 곳에서 (기관·금액·법인 3조건).

# 사이트별 분야 필터 — 여기 명시된 사이트만 해당 분야 글로 제한.
# 사이트 category(명부 표시용)와 별개: 통합 게시판에 category가 한쪽으로 적힌 곳이 많아
# (용인시-토목·연천군 맑은물 등) 전역 적용 시 정상 글이 대량 누락됨.
# 제목에 분야 표시 없는 글은 통과 — 놓침 방지.
# 부천시 제거(2026-08-05): notifier.ARCH_NOTIFY_BLOCKED_SITES로 건축 '알림'을 막게 되어
# 수집까지 제한할 이유가 없어짐 — 건축 공고도 bids에 쌓아 모집·등록명부 추적에 활용.
SITE_CATEGORY_FILTER: dict[str, str] = {}


def _env_true(name: str) -> bool:
    import os
    return os.getenv(name, "").lower() in ("1", "true", "yes")


def _notify_disabled() -> bool:
    # .env의 NOTIFY_DISABLED=true 면 슬랙 발송 일시 중지.
    # 크롤링·DB·LLM은 계속 돌고, 발송만 skip → 다시 켜면 보류된 글 한꺼번에 발송.
    return _env_true("NOTIFY_DISABLED")


def _notify_via_hub() -> bool:
    """알림 주체가 허브(sales-hub 용역허브 채널)인가 — .env의 NOTIFY_VIA_HUB=true (2026-08-11 일원화).

    _notify_disabled와 다르다: 저쪽은 '나중에 몰아서 보낼 보류'라 notified=False로 남기지만,
    이쪽은 허브가 대신 보내므로 notified=True로 닫는다(안 그러면 미발송이 끝없이 쌓인다).
    허브는 arch_bid_notices.notified_at이 빈 공고를 아침에 알리므로 hub_sync가 그 칸을 비워 둔다.
    """
    return _env_true("NOTIFY_VIA_HUB")


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
            # 분야 필터 — SITE_CATEGORY_FILTER에 명시된 사이트만 적용.
            # 분야 표시 없는 일반 공고("안전점검 수행기관 모집")는 통과 — 놓침 방지.
            want_cat = SITE_CATEGORY_FILTER.get(site.name)
            if want_cat:
                post_cat = _classify_post_category(posting.title)
                if post_cat in ("건축", "토목") and post_cat != want_cat:
                    logger.info(
                        "[%s] skip(분야 불일치: 수집=%s, 글=%s): %s",
                        site.name, want_cat, post_cat, posting.title[:40],
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
                            src, pdf = att_mod.prepare_for_upload(
                                a.url, a.name, record["url"] or "", work,
                                session=adapter.session,
                            )
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

                        # 결과 공고면 선정업체·금액도 추출해 덧붙임 (게시판 전용 건의 유일한 결과 원천).
                        # 기존 7필드를 덮지 않도록 merge 사용.
                        if summarizer.is_result_notice(record["title"]):
                            result = summarizer.extract_result_fields(record["title"], body_for_llm)
                            if result.get("selected_company") or result.get("selected_price"):
                                store.merge_bid_extracted_fields(record["notice_id"], result)
                                extracted = {**extracted, **result}
                                logger.info(
                                    "[%s] 결과 추출: %s / %s",
                                    site.name, result.get("selected_company") or "업체미상",
                                    f"{result['selected_price']:,}원" if result.get("selected_price") else "금액미상",
                                )
                    except Exception as ex:
                        logger.warning("[%s] LLM 요약 실패 (%s): %s", site.name, record["notice_id"], ex)

                # 3) Slack 즉시 발송 (한 공고 = 한 메시지 + thread에 첨부)
                row_for_send = dict(record)
                row_for_send["extracted_fields"] = extracted
                # 슬랙 채널 attach 실패 케이스 대비 — 본문에 원본 다운로드 URL 함께 노출
                row_for_send["attachments_raw"] = [
                    {"name": a.name, "url": a.url} for a in (posting.attachments or [])[:10]
                ]
                if _notify_via_hub():
                    # 알림 주체가 허브 — 자체 슬랙은 안 보내고 notified만 닫는다.
                    # 허브는 아래 upsert된 행(notified_at=null)을 아침 알림에 싣는다.
                    store.mark_notified([record["notice_id"]])
                    logger.info(
                        "[%s] 자체 발송 생략(허브 알림): %s",
                        site.name, record["title"][:30],
                    )
                elif _notify_disabled():
                    # 발송 보류 — notified=False 유지로 다시 켰을 때 누락 없이 받음
                    logger.info(
                        "[%s] 발송 보류 (NOTIFY_DISABLED=true): %s",
                        site.name, record["title"][:30],
                    )
                elif should_skip_arch_notify(site.name, record["title"]):
                    # 건축 투찰 불가 발주청의 건축 공고 — 알림만 제외, DB 저장은 유지.
                    # notified 처리해서 fallback이 매 사이클 재시도하지 않게 한다.
                    store.mark_notified([record["notice_id"]])
                    logger.info(
                        "[skip] %s 건축 공고 알림 제외(투찰불가): %s",
                        site.name, record["title"][:45],
                    )
                elif send_one_posting(cfg, row_for_send, file_paths):
                    store.mark_notified([record["notice_id"]])
                    logger.info("[%s] 발송 완료: %s (첨부 %d개)", site.name, record["title"][:30], len(file_paths))
                else:
                    logger.warning("[%s] 발송 실패: %s", site.name, record["title"][:30])

                # 4) 허브 arch_bid_notices에도 upsert (source='local') — 허브 입찰 관리 화면 합류.
                # notified_at은 비운다 — 허브가 이 공고를 아침 알림에 실어야 한다.
                # 실패해도 수집에는 영향 없음 (upsert_bid 내부에서 예외 흡수).
                try:
                    # extracted를 함께 넘겨야 presmpt_price(안전점검비용)가 채워진다 —
                    # 허브가 이 값을 분모로 낙찰률을 계산한다
                    # 첨부는 posting에서 직접 넘긴다 — bids엔 첨부 컬럼이 없어 나중엔 못 구한다
                    if hub_sync.upsert_bid(record, extracted=extracted,
                                           attachments=posting.attachments):
                        result_ef = extracted or {}
                        if result_ef.get("selected_company") or result_ef.get("selected_price"):
                            hub_sync.upsert_award(
                                record["notice_id"],
                                result_ef.get("selected_company"),
                                result_ef.get("selected_price"),
                            )
                except Exception:
                    logger.exception("[hub] upsert 중 예외 (수집·알림 영향 없음)")
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

    # cron 시작 시 LLM 인증 사전 체크 — 진짜 만료(401)만 LLM skip + admin 알림.
    # 일시적 proxy 지연은 ok=True + 경고 사유 → 로그만 남기고 LLM 시도 계속.
    global _LLM_AUTH_OK
    ok, reason = summarizer.check_auth()
    _LLM_AUTH_OK = ok
    if ok and reason:
        logger.warning("LLM 사전체크 경고(무시하고 진행): %s", reason)
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

    if new_rows and _notify_via_hub():
        # 허브가 알린다 — 자체 발송 없이 notified만 닫아 미발송이 쌓이지 않게 한다
        store.mark_notified([str(r["notice_id"]) for r in new_rows])
        logger.info("NOTIFY_VIA_HUB=true — fallback %d건 자체 발송 생략(허브 알림)", len(new_rows))
    elif new_rows and _notify_disabled():
        logger.info("NOTIFY_DISABLED=true — fallback 알림 %d건 보류", len(new_rows))
    elif new_rows:
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

    # 사이클 맨 끝 — 허브 arch_bid_notices 나라장터 공고문 요건 추출.
    # 전체를 격리: 여기서 무슨 일이 나도 위의 지자체 수집·알림에는 영향 없음.
    # LLM 인증이 이미 만료로 판정됐으면(_LLM_AUTH_OK=False) 이번 사이클은 건너뜀.
    try:
        if not _LLM_AUTH_OK:
            logger.info("[g2b] LLM 인증 실패 상태 — 이번 사이클 추출 skip")
        else:
            from . import g2b_requirements
            g2b_requirements.run()
    except Exception:
        logger.exception("[g2b] 요건 추출 실패 (지자체 수집·알림에는 영향 없음)")


def main() -> int:
    try:
        run_once()
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
