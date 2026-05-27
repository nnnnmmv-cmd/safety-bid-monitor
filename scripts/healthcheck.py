"""안전진단 모니터 헬스체크 — 외부 의존성 + 사이트 fetch 일괄 점검.

사용 예:
    .venv/bin/python scripts/healthcheck.py            # 빠른 점검 (외부 + 샘플 5개)
    .venv/bin/python scripts/healthcheck.py --full     # 모든 활성 사이트 fetch
    .venv/bin/python scripts/healthcheck.py --slack    # 결과를 admin 슬랙 채널로 발송
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config


# 결과 누적 — 마지막에 슬랙 발송용
_RESULTS: list[str] = []


def _say(line: str) -> None:
    print(line)
    _RESULTS.append(line)


def check_env_and_db() -> object | None:
    _say("\n=== 1. 환경 + Supabase ===")
    try:
        cfg = load_config()
        from src import store
        sites = store.list_sites()
        enabled = [s for s in sites if s.get("enabled")]
        kw = store.list_keywords()
        _say(f"  OK  Supabase 연결")
        _say(f"      사이트 전체 {len(sites)}건 / 활성 {len(enabled)}건")
        _say(f"      키워드 include={len(kw.get('include',[]))}, exclude={len(kw.get('exclude',[]))}")
        return cfg
    except Exception as e:
        _say(f"  FAIL  Supabase: {e}")
        return None


def check_openclaw() -> bool:
    _say("\n=== 2. openclaw LLM proxy (Claude Max 인증) ===")
    import requests
    url = os.getenv("OPENCLAW_PROXY_URL", "http://localhost:3456/v1/chat/completions")
    try:
        r = requests.post(
            url,
            json={
                "model": os.getenv("OPENCLAW_MODEL", "claude-sonnet-4-5"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 20,
            },
            timeout=15,
        )
        if r.status_code != 200:
            _say(f"  FAIL  HTTP {r.status_code}: {r.text[:200]}")
            return False
        content = (
            r.json().get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        )
        if (
            "Failed to authenticate" in content[:80]
            or '"type":"authentication_error"' in content[:200]
            or 'authentication_error' in content[:200]
            or content.startswith("401")
        ):
            _say(f"  FAIL  Claude Max OAuth 만료")
            _say(f"        조치: 터미널에서 'claude' 실행 → '/login' → 브라우저 로그인")
            _say(f"        이후: launchctl kickstart -k gui/$(id -u)/com.openclaw.claude-max-proxy")
            _say(f"        raw: {content[:160]!r}")
            return False
        _say(f"  OK  응답 수신 ({len(content)}자): {content[:60]!r}")
        return True
    except requests.ConnectionError:
        _say(f"  FAIL  proxy 응답 없음 ({url})")
        _say(f"        조치: launchctl list | grep openclaw — 프로세스 확인")
        return False
    except Exception as e:
        _say(f"  FAIL  {type(e).__name__}: {e}")
        return False


def check_slack(cfg: object) -> bool:
    _say("\n=== 3. Slack Bot ===")
    if not (cfg.slack and cfg.slack.bot_token):  # type: ignore[attr-defined]
        _say("  WARN  Bot token 미설정")
        return False
    try:
        from slack_sdk import WebClient
        client = WebClient(token=cfg.slack.bot_token)  # type: ignore[attr-defined]
        r = client.auth_test()
        if not r.get("ok"):
            _say(f"  FAIL  auth.test: {r.data}")
            return False
        _say(f"  OK  bot={r.get('user')} team={r.get('team')}")
        for label, ch_id in (
            ("건축", cfg.slack.channel_building),  # type: ignore[attr-defined]
            ("토목", cfg.slack.channel_civil),  # type: ignore[attr-defined]
        ):
            if not ch_id:
                _say(f"  WARN  {label} 채널 ID 미설정")
            elif not ch_id.startswith(("C", "G", "D")):
                _say(f"  FAIL  {label} 채널 ID 형식 이상: {ch_id}")
            else:
                _say(f"  OK    {label} 채널 {ch_id}")
        return True
    except Exception as e:
        _say(f"  FAIL  {type(e).__name__}: {e}")
        return False


def check_sites(cfg: object, limit: int) -> None:
    enabled = list(cfg.sites)[:limit]  # type: ignore[attr-defined]
    _say(f"\n=== 4. 사이트 fetch 검증 ({len(enabled)}개) ===")
    from src.adapters.registry import build_adapter
    since = datetime.now() - timedelta(hours=cfg.runtime.lookback_hours)  # type: ignore[attr-defined]
    ok_cnt = warn_cnt = fail_cnt = 0
    for site in enabled:
        try:
            adapter = build_adapter(site, cfg.runtime)  # type: ignore[attr-defined]
            adapter.prefilter_titles = cfg.keywords.include  # type: ignore[attr-defined]
            postings = adapter.fetch(since)
            cnt = len(postings)
            if cnt == 0:
                _say(f"  WARN  {site.name}: raw=0")
                warn_cnt += 1
            else:
                # 첫 글 title/url 검증
                first = postings[0]
                title_ok = bool(first.title.strip())
                url_ok = bool(first.url and first.url.startswith("http"))
                if title_ok and url_ok:
                    _say(f"  OK    {site.name}: raw={cnt}")
                    ok_cnt += 1
                else:
                    _say(f"  WARN  {site.name}: raw={cnt} title_ok={title_ok} url_ok={url_ok}")
                    warn_cnt += 1
        except Exception as e:
            _say(f"  FAIL  {site.name}: {type(e).__name__}: {str(e)[:100]}")
            fail_cnt += 1
    _say(f"\n      합계: OK {ok_cnt} / WARN {warn_cnt} / FAIL {fail_cnt}")


def maybe_send_slack(cfg: object) -> None:
    if not (cfg.slack and cfg.slack.bot_token):  # type: ignore[attr-defined]
        return
    admin = cfg.slack.channel_building or cfg.slack.channel_civil  # type: ignore[attr-defined]
    if not admin:
        return
    try:
        from slack_sdk import WebClient
        client = WebClient(token=cfg.slack.bot_token)  # type: ignore[attr-defined]
        text = "*🩺 안전진단 모니터 헬스체크*\n```" + "\n".join(_RESULTS)[:2800] + "```"
        client.chat_postMessage(channel=admin, text=text, mrkdwn=True)
        print("\n(슬랙 발송 완료)")
    except Exception as e:
        print(f"\n(슬랙 발송 실패: {e})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="모든 활성 사이트 검증")
    parser.add_argument("--slack", action="store_true", help="결과를 슬랙 admin 채널로 발송")
    parser.add_argument("--limit", type=int, default=8, help="빠른 모드에서 검증할 사이트 수")
    args = parser.parse_args()

    _say("=" * 60)
    _say(f"  안전진단 모니터 헬스체크 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    _say("=" * 60)
    cfg = check_env_and_db()
    if cfg is None:
        return 1
    check_openclaw()
    check_slack(cfg)
    check_sites(cfg, limit=999 if args.full else args.limit)
    _say("\n=== 끝 ===")
    if args.slack:
        maybe_send_slack(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
