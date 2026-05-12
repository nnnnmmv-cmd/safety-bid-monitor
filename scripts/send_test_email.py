"""Gmail SMTP 설정과 수신자 도달 여부를 검증한다."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.notifier import render_html, render_text, send_email


def main() -> int:
    cfg = load_config()
    if cfg.smtp is None:
        print("ERROR: .env에 SMTP_USER / SMTP_APP_PASSWORD / NOTIFY_TO를 설정하세요.")
        return 2
    sample = [
        {
            "site_name": "테스트 발주청",
            "title": "건설공사 안전점검 수행기관 지정 공고 (테스트)",
            "deadline_at": datetime.now().isoformat(timespec="seconds"),
            "estimated_price": 12_500_000,
            "url": "https://example.go.kr/board/view?id=1",
        }
    ]
    send_email(
        cfg.smtp,
        subject="[안전진단 모니터] 발송 테스트",
        text_body=render_text(sample),
        html_body=render_html(sample),
    )
    print(f"OK — sent to {', '.join(cfg.smtp.notify_to)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
