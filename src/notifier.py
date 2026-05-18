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


def render_slack(rows: Sequence[dict[str, object]]) -> str:
    if not rows:
        return "신규 공고가 없습니다."
    grouped = _group_by_site(rows)
    lines: list[str] = [f"*🛠 안전진단 신규 공고 {len(rows)}건*", ""]
    for site, items in grouped.items():
        cat = str(items[0].get("category") or "").strip()
        tag = f" `[{_slack_escape(cat)}]`" if cat else ""
        lines.append(f"*▶ {_slack_escape(site)}*{tag} _({len(items)}건)_")
        for item in items:
            title = _slack_escape(str(item.get("title") or "(제목 없음)"))
            url = str(item.get("url") or "")
            deadline = _parse_iso(item.get("deadline_at"))
            dday = d_day_label(deadline) if deadline else ""
            deadline_text = deadline.strftime("%Y-%m-%d") if deadline else "미정"
            link = f"<{url}|{title}>" if url else title
            lines.append(f"• {link}")
            lines.append(f"   마감 `{deadline_text}` {dday} | 추정가 `{_price_text(item)}`")
        lines.append("")
    return "\n".join(lines)


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


def notify_new_postings(cfg: AppConfig, rows: Sequence[dict[str, object]]) -> None:
    if not rows:
        return
    if cfg.slack:
        by_channel: dict[str, list[dict[str, object]]] = {}
        unrouted = 0
        for row in rows:
            targets = _resolve_targets(cfg.slack, str(row.get("category") or ""))
            if not targets:
                unrouted += 1
                continue
            for url in targets:
                by_channel.setdefault(url, []).append(dict(row))
        for url, items in by_channel.items():
            send_slack(url, render_slack(items))
        if unrouted:
            logger.warning("Slack webhook 매칭 안 된 공고 %d건 (카테고리 빈 값)", unrouted)
        logger.info("Slack 알림 발송 완료 (%d건 → %d개 채널)", len(rows) - unrouted, len(by_channel))
        return
    if cfg.smtp:
        send_email(
            cfg.smtp,
            subject=f"[안전진단 모니터] 신규 공고 {len(rows)}건",
            text_body=render_text(rows),
            html_body=render_html(rows),
        )
        logger.info("이메일 알림 발송 완료 (%d건)", len(rows))
        return
    logger.warning("Slack/SMTP 설정이 없어 알림 발송 생략 (%d건)", len(rows))


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
