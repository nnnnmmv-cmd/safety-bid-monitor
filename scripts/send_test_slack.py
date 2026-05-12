"""Slack Incoming Webhook 설정과 도달 여부를 검증한다."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.notifier import render_slack, send_slack


def main() -> int:
    cfg = load_config()
    if cfg.slack is None:
        print("ERROR: .env에 SLACK_WEBHOOK_URL을 설정하세요.")
        return 2
    sample = [
        {
            "site_name": "테스트 발주청",
            "title": "건설공사 안전점검 수행기관 지정 공고 (테스트)",
            "deadline_at": datetime.now().isoformat(timespec="seconds"),
            "estimated_price": 12_500_000,
            "url": "https://example.go.kr/board/view?id=1",
        },
        {
            "site_name": "테스트 발주청",
            "title": "정밀안전점검 용역 입찰 공고 (테스트)",
            "deadline_at": None,
            "estimated_price": None,
            "url": "https://example.go.kr/board/view?id=2",
        },
    ]
    send_slack(cfg.slack.webhook_url, render_slack(sample))
    print("OK — sent to Slack webhook")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
