from __future__ import annotations

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Sequence

from .config import AppConfig, SlackConfig, SmtpConfig
from .utils import d_day_label, format_price

logger: logging.Logger = logging.getLogger(__name__)


def _group_by_site(rows: Sequence[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        site = str(row.get("site_name") or "기타")
        grouped.setdefault(site, []).append(dict(row))
    return grouped


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _price_text(row: dict[str, object]) -> str:
    price_raw = row.get("estimated_price")
    return format_price(price_raw if isinstance(price_raw, int) else None)


# ---------------- Email rendering ----------------

def _category_tag(items: list[dict[str, object]]) -> str:
    cat = str(items[0].get("category") or "").strip() if items else ""
    return f"[{cat}] " if cat else ""


def render_text(rows: Sequence[dict[str, object]]) -> str:
    if not rows:
        return "신규 공고가 없습니다."
    grouped = _group_by_site(rows)
    lines: list[str] = [f"[안전진단 신규 공고 {len(rows)}건]", ""]
    for site, items in grouped.items():
        lines.append(f"▶ {_category_tag(items)}{site} ({len(items)}건)")
        for idx, item in enumerate(items, start=1):
            title = item.get("title") or "(제목 없음)"
            deadline = _parse_iso(item.get("deadline_at"))
            dday = d_day_label(deadline) if deadline else ""
            deadline_text = deadline.strftime("%Y-%m-%d") if deadline else "미정"
            url = item.get("url") or ""
            lines.append(f"  {idx}. {title}")
            lines.append(f"     마감 {deadline_text} {dday} | 추정가 {_price_text(item)}")
            if url:
                lines.append(f"     {url}")
        lines.append("")
    return "\n".join(lines)


def render_html(rows: Sequence[dict[str, object]]) -> str:
    if not rows:
        return "<p>신규 공고가 없습니다.</p>"
    grouped = _group_by_site(rows)
    parts: list[str] = [
        "<html><body style='font-family:system-ui,sans-serif;'>",
        f"<h2>안전진단 신규 공고 {len(rows)}건</h2>",
    ]
    for site, items in grouped.items():
        parts.append(f"<h3>{_category_tag(items)}{site} <small>({len(items)}건)</small></h3>")
        parts.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>")
        parts.append(
            "<tr style='background:#f4f4f4;'>"
            "<th>공고명</th><th>마감</th><th>D-Day</th><th>추정가</th><th>링크</th>"
            "</tr>"
        )
        for item in items:
            title = item.get("title") or "(제목 없음)"
            deadline = _parse_iso(item.get("deadline_at"))
            dday = d_day_label(deadline) if deadline else ""
            deadline_text = deadline.strftime("%Y-%m-%d") if deadline else "-"
            url = item.get("url") or ""
            link_html = f"<a href='{url}'>열기</a>" if url else "-"
            parts.append(
                f"<tr><td>{title}</td><td>{deadline_text}</td>"
                f"<td>{dday}</td><td>{_price_text(item)}</td><td>{link_html}</td></tr>"
            )
        parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts)


def send_email(
    smtp: SmtpConfig,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    to_override: list[str] | None = None,
) -> None:
    recipients = to_override or smtp.notify_to
    if not recipients:
        raise ValueError("이메일 수신자가 비어있습니다 (.env의 NOTIFY_TO 확인)")

    msg = EmailMessage()
    msg["From"] = smtp.user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(smtp.user, smtp.app_password)
        server.send_message(msg)


# ---------------- Slack rendering ----------------

def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_FIELD_LABELS: list[tuple[str, str]] = [
    ("inspection_cost", "안전점검비용"),
    ("contractor", "시공자"),
    ("scale", "규모"),
    ("bid_period", "접수방법"),
    ("evaluation_method", "평가방법"),
    ("low_bid_rate", "낙찰하한율"),
    ("winner_selection", "낙찰자 선정"),
]


def _render_one_card(item: dict[str, object]) -> list[str]:
    """공고 1건의 Slack 메시지 카드."""
    title = _slack_escape(str(item.get("title") or "(제목 없음)"))
    site = _slack_escape(str(item.get("site_name") or ""))
    url = str(item.get("url") or "")
    title_line = f"*[{site}] {title}*" if site else f"*{title}*"

    body_lines: list[str] = []
    fields = item.get("extracted_fields") or {}
    if isinstance(fields, dict) and any(fields.values()):
        for key, label in _FIELD_LABELS:
            val = str(fields.get(key) or "").strip()
            if val:
                body_lines.append(f"• *{label}* : {_slack_escape(val)}")
    else:
        deadline = _parse_iso(item.get("deadline_at"))
        if deadline:
            dday = d_day_label(deadline)
            body_lines.append(f"• *마감* : {deadline.strftime('%Y-%m-%d')} {dday}")
        price_raw = item.get("estimated_price")
        if isinstance(price_raw, int) and price_raw > 0:
            body_lines.append(f"• *추정가* : {_price_text(item)}")

    if not body_lines:
        body_lines.append("• _(상세 정보 분석 중 또는 본문 정보 부족 — 첨부 파일을 확인해주세요)_")

    lines: list[str] = [title_line, ""]
    lines.extend(body_lines)
    if url:
        lines.append("")
        lines.append(f"🔗 <{url}|공고 원문 보기>")
    return lines


def render_slack(rows: Sequence[dict[str, object]]) -> str:
    if not rows:
        return "신규 공고가 없습니다."
    # 헤더 — 카테고리 그룹 (한 채널엔 보통 한 카테고리만 옴)
    cats: list[str] = []
    seen: set[str] = set()
    for r in rows:
        c = str(r.get("category") or "").strip()
        if c and c not in seen:
            cats.append(c); seen.add(c)
    cat_label = " / ".join(cats) if cats else "기타"
    header = f"*🛠 안전진단 신규 공고 {len(rows)}건* `[{_slack_escape(cat_label)}]`"

    parts: list[str] = [header, ""]
    for i, item in enumerate(rows):
        if i > 0:
            parts.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        parts.extend(_render_one_card(item))
        parts.append("")
    return "\n".join(parts)


def send_slack(webhook_url: str, text: str, http_timeout: float = 15.0) -> None:
    if not webhook_url:
        raise ValueError("Slack webhook URL이 비어있습니다 (.env의 SLACK_WEBHOOK_URL 확인)")
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 300 or body.strip() != "ok":
                raise RuntimeError(f"Slack webhook returned status={resp.status} body={body[:200]}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Slack webhook HTTP {exc.code}: {detail[:200]}") from exc


# ---------------- Dispatch ----------------

_BOTH_CATEGORIES: set[str] = {"건축·토목", "건축/토목", "토목/건축", "토목·건축"}


def _resolve_targets(slack: SlackConfig, category: str) -> list[str]:
    """공고의 category에 따라 발송할 Slack webhook URL 목록 반환.

    - 건축 → 건축 채널 (없으면 fallback)
    - 토목 → 토목 채널 (없으면 fallback → 건축 임시)
    - 건축·토목 → 양쪽 모두 (없으면 fallback)
    - 카테고리 없음 → fallback (없으면 건축 또는 토목)
    """
    cat = (category or "").strip()
    b, c, f = slack.building_webhook_url, slack.civil_webhook_url, slack.webhook_url
    if cat == "건축":
        return [b] if b else ([f] if f else [])
    if cat == "토목":
        return [c] if c else ([f] if f else ([b] if b else []))
    if cat in _BOTH_CATEGORIES:
        targets = [u for u in (b, c) if u]
        return targets if targets else ([f] if f else [])
    return [f] if f else ([b] if b else ([c] if c else []))


def _resolve_channel_ids(slack: SlackConfig, category: str) -> list[str]:
    """Bot 모드: category → 채널 ID 리스트."""
    cat = (category or "").strip()
    b, c = slack.channel_building, slack.channel_civil
    if cat == "건축":
        return [b] if b else []
    if cat == "토목":
        return [c] if c else ([b] if b else [])
    if cat in _BOTH_CATEGORIES:
        targets = [x for x in (b, c) if x]
        return targets if targets else []
    return [b] if b else [c] if c else []


def send_card_with_attachments(
    slack: SlackConfig,
    channel_id: str,
    card_text: str,
    file_paths: list,
) -> bool:
    """Slack Bot으로 메시지 발송 후 같은 thread에 파일 업로드."""
    if not slack.bot_token or not channel_id:
        return False
    try:
        from slack_sdk import WebClient
        client = WebClient(token=slack.bot_token)
        resp = client.chat_postMessage(channel=channel_id, text=card_text, mrkdwn=True)
        ts = resp.get("ts")
        if file_paths and ts:
            uploads = [{"file": str(f), "filename": f.name} for f in file_paths if f and f.exists()]
            if uploads:
                up = client.files_upload_v2(channel=channel_id, thread_ts=ts, file_uploads=uploads)
                if not up.get("ok"):
                    logger.warning("Slack 파일 업로드 응답 ok=False: %s", up.data)
        return True
    except Exception as exc:
        logger.warning("Slack Bot 발송 실패 (%s): %s", channel_id, exc)
        return False


def notify_new_postings(cfg: AppConfig, rows: Sequence[dict[str, object]]) -> None:
    """기존 호출처 호환용. 첨부파일 없이 발송. monitor는 send_one_posting을 직접 호출 권장."""
    if not rows:
        return
    if cfg.slack and cfg.slack.bot_token and (cfg.slack.channel_building or cfg.slack.channel_civil):
        sent = 0
        for row in rows:
            targets = _resolve_channel_ids(cfg.slack, str(row.get("category") or ""))
            if not targets:
                continue
            text = "\n".join(_render_one_card(dict(row)))
            for ch in targets:
                if send_card_with_attachments(cfg.slack, ch, text, []):
                    sent += 1
        logger.info("Slack Bot 발송 (첨부 없음): %d건 (rows=%d)", sent, len(rows))
        return
    if cfg.slack and (cfg.slack.building_webhook_url or cfg.slack.civil_webhook_url or cfg.slack.webhook_url):
        # 구 webhook fallback
        by_channel: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            targets = _resolve_targets(cfg.slack, str(row.get("category") or ""))
            for url in targets:
                by_channel.setdefault(url, []).append(dict(row))
        for url, items in by_channel.items():
            send_slack(url, render_slack(items))
        logger.info("Slack webhook 발송 (%d채널)", len(by_channel))
        return
    if cfg.smtp:
        send_email(cfg.smtp, f"[안전진단 모니터] 신규 공고 {len(rows)}건",
                   render_text(rows), render_html(rows))
        return
    logger.warning("Slack/SMTP 미설정 (%d건)", len(rows))


def send_one_posting(cfg: AppConfig, row: dict, file_paths: list) -> bool:
    """공고 1건 + 첨부파일을 카테고리 채널에 발송 (메시지 + thread reply)."""
    if not cfg.slack or not cfg.slack.bot_token:
        return False
    targets = _resolve_channel_ids(cfg.slack, str(row.get("category") or ""))
    if not targets:
        logger.warning("[%s] 매칭 채널 없음 — category=%r", row.get("site_name"), row.get("category"))
        return False
    text = "\n".join(_render_one_card(dict(row)))
    ok_any = False
    for ch in targets:
        if send_card_with_attachments(cfg.slack, ch, text, file_paths):
            ok_any = True
    return ok_any


def notify_error(cfg: AppConfig, summary: str, detail: str) -> None:
    body = f"{summary}\n\n---\n{detail}"
    if cfg.slack:
        send_slack(cfg.slack.admin_webhook_url, f"*⚠️ 안전진단 모니터 에러*\n```{body[:2000]}```")
        return
    if cfg.smtp and cfg.smtp.notify_admin:
        send_email(
            cfg.smtp,
            subject=f"[안전진단 모니터][에러] {summary[:60]}",
            text_body=body,
            to_override=[cfg.smtp.notify_admin],
        )
        return
    logger.error("에러 알림 채널이 설정되지 않음: %s", summary)
