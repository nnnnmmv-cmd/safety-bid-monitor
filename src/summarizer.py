"""공고 본문에서 사용자가 요청한 7개 필드를 LLM으로 추출.

claude-max-api-proxy (localhost:3456) 의 OpenAI 호환 API 호출.
proxy가 안 떠 있거나 실패 시 빈 dict 반환 (모니터 전체가 멈추지 않게).
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

logger: logging.Logger = logging.getLogger(__name__)

OPENCLAW_PROXY_URL: str = os.getenv("OPENCLAW_PROXY_URL", "http://localhost:3456/v1/chat/completions")
OPENCLAW_MODEL: str = os.getenv("OPENCLAW_MODEL", "claude-sonnet-4-5")
SUMMARIZE_TIMEOUT: float = float(os.getenv("SUMMARIZE_TIMEOUT_SEC", "60"))

EMPTY_FIELDS: dict[str, str] = {
    "inspection_cost": "",
    "contractor": "",
    "scale": "",
    "bid_period": "",
    "evaluation_method": "",
    "low_bid_rate": "",
    "winner_selection": "",
}

SYSTEM_PROMPT: str = """당신은 한국 공공 건설공사 안전점검 입찰공고를 분석하는 전문가입니다.
주어진 공고 제목과 본문에서 아래 7개 필드를 추출하세요.

추출 규칙:
- 각 필드는 본문에 있는 정보를 사람이 읽기 쉽게 자연스러운 한국어로 정리
- 본문에 없는 정보는 빈 문자열("")로 둘 것
- 추측하지 말 것. 명시되지 않은 정보는 빈 문자열
- 숫자/금액/날짜는 본문 그대로의 형태 유지 (예: "47,983,663원", "2026-05-08 10:00")

필드 정의:
- inspection_cost: 안전점검비용 또는 추정가격 (예: "47,983,663원(안전점검 검토 비용, VAT별도)")
- contractor: 시공자/시공사 (예: "한내종합건설(주)")
- scale: 공사 규모 — 연면적·층수·동수 등 (예: "연 면적 41,216.41㎡ / 지상 1-5층, 8동")
- bid_period: 입찰서 접수기간 + 입찰 방식 (예: "2026-05-08 10:00 ~ 2026-05-15 10:00 (전자입찰)")
- evaluation_method: 평가방법 (예: "입찰 가격 100%")
- low_bid_rate: 낙찰하한율 (예: "87.745%")
- winner_selection: 낙찰자 선정 방식 (예: "나라장터 가격순위. 참가자 전원 낙찰하한율 미달 시 가장 근접한 자")

응답 형식 — 반드시 아래 JSON 한 줄로만 출력. 마크다운 코드블록·설명·인사 금지:
{"inspection_cost":"...","contractor":"...","scale":"...","bid_period":"...","evaluation_method":"...","low_bid_rate":"...","winner_selection":"..."}"""


# ---------------- 결과 공고(지정/선정 결과) 추출 ----------------
# 게시판 전용 건은 이 공고문이 "어느 업체가 얼마에 선정됐는지"의 유일한 원천이다
# (나라장터 경유 건은 허브가 API로 결과를 받음).
# "선정"은 '수행기관 선정 모집 공고'처럼 모집 단계에도 쓰여 (?!.*모집)로 배제.
RESULT_TITLE_RE: re.Pattern[str] = re.compile(r"결과|선정(?!.*모집)|낙찰")

RESULT_KEYS: tuple[str, ...] = ("result_kind", "selected_company", "selected_price", "target_project")

RESULT_SYSTEM_PROMPT: str = """너는 한국 공공 안전점검 '지정/선정 결과 공고'에서 결과 사실만 그대로 뽑아내는 도구다.

규칙:
- 공고문에 적힌 표현을 원문 그대로 인용한다. 해석·요약·추론하지 마라.
- 공고문에 없으면 반드시 null. 절대 지어내지 마라.
- selected_price는 숫자만 (쉼표·원 제거, 부가세 포함 여부는 무시). 금액이 없으면 null.
- 여러 업체가 순위별로 나오면 1순위(우선순위자)를 selected_company로 한다.
- 설명 문장 없이 JSON만 출력한다.

출력 형식:
{"result_kind":"지정결과|선정결과|낙찰결과 중 공고문 표현에 가까운 것","selected_company":"선정 업체명 원문 (없으면 null)","selected_price":숫자 또는 null,"target_project":"대상 공사명 원문 (없으면 null)"}"""


def is_result_notice(title: str) -> bool:
    """제목이 결과류(지정/선정 결과·낙찰)인지."""
    return bool(RESULT_TITLE_RE.search(title or ""))


def _to_price(value: Any) -> int | None:
    """'1,234,000원' / 1234000 / None → int | None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n = int(value)
        return n if 0 < n < 10**12 else None
    digits = re.sub(r"[^0-9]", "", str(value))
    if not digits:
        return None
    n = int(digits)
    return n if 0 < n < 10**12 else None


def extract_result_fields(title: str, body: str) -> dict[str, Any]:
    """결과 공고에서 선정업체·금액·대상공사 추출. 실패 시 빈 dict (호출측이 병합 skip)."""
    body = (body or "")[:12000]
    if not title and not body:
        return {}

    payload: dict[str, Any] = {
        "model": OPENCLAW_MODEL,
        "messages": [
            {"role": "system", "content": RESULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"공고 제목: {title}\n\n공고 본문:\n{body}"},
        ],
        "max_tokens": 600,
        "temperature": 0.1,
    }
    try:
        r = requests.post(OPENCLAW_PROXY_URL, json=payload, timeout=SUMMARIZE_TIMEOUT)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("결과 추출 LLM 실패 (title=%s...): %s", (title or "")[:30], exc)
        return {}

    if content and ("authentication_error" in content[:200] or "Failed to authenticate" in content[:80]):
        logger.warning("결과 추출 — LLM 인증 만료")
        return {}

    parsed = _extract_json(content)
    if not isinstance(parsed, dict):
        logger.warning("결과 추출 JSON 파싱 실패. raw=%r", (content or "")[:160])
        return {}

    out: dict[str, Any] = {}
    for k in RESULT_KEYS:
        v = parsed.get(k)
        if k == "selected_price":
            out[k] = _to_price(v)
        else:
            s = str(v).strip() if v not in (None, "", "null") else ""
            out[k] = s or None
    # result_kind는 비어도 결과 공고임은 확실하므로 기본값 보정
    if not out.get("result_kind"):
        out["result_kind"] = "지정결과"
    return out


def _extract_json(text: str) -> dict[str, str] | None:
    """LLM이 ```json ... ``` 또는 설명+JSON 형태로 반환할 수 있어 안전하게 JSON 부분만 추출."""
    text = text.strip()
    # 마크다운 코드블록 벗기기
    if text.startswith("```"):
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    # JSON 객체 부분만 추출
    if not text.startswith("{"):
        m = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, re.DOTALL)
        if m:
            text = m.group(1)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return {k: str(v) if v else "" for k, v in data.items()}
    except json.JSONDecodeError:
        return None
    return None


def is_available() -> bool:
    """proxy 서버가 응답하는지 확인."""
    try:
        base = OPENCLAW_PROXY_URL.rsplit("/v1/", 1)[0]
        r = requests.get(f"{base}/health", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def check_auth() -> tuple[bool, str]:
    """LLM 실제 호출 → (OK?, 실패 시 사유). Claude Max OAuth 만료 감지용.

    monitor.run_once 시작 시 1회 호출. 만료면 LLM 호출 모두 skip + admin 알림.
    cron 정각엔 proxy가 일시적으로 느려질 수 있어 타임아웃 여유(45초) + 2회 재시도.
    timeout/연결 오류는 '만료'가 아니라 '일시적 지연'이므로 인증 실패로 판정하지 않음
    (실제 cron은 LLM 호출을 계속 시도 — extract_bid_fields가 개별 처리).
    """
    import time as _time
    last_reason = ""
    # 401 만료도 일시적인 경우가 있어 재시도 — proxy가 토큰 자동 갱신할 시간을 줌.
    for attempt in range(3):
        try:
            r = requests.post(
                OPENCLAW_PROXY_URL,
                json={
                    "model": OPENCLAW_MODEL,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
                timeout=45,
            )
            if r.status_code != 200:
                last_reason = f"HTTP {r.status_code}: {r.text[:120]}"
            else:
                content = (r.json().get("choices", [{}])[0].get("message", {}).get("content") or "")
                if (
                    "Failed to authenticate" in content[:80]
                    or "authentication_error" in content[:200]
                    or content.startswith("401")
                ):
                    last_reason = "Claude Max OAuth 만료 — /login 필요"
                else:
                    return True, ""  # 정상
        except requests.RequestException as exc:
            last_reason = f"proxy 응답 없음: {exc}"
        if attempt < 2:
            _time.sleep(8)  # 재인증/proxy 깨어날 시간

    # 3회 모두 실패. 401(만료)이면 진짜 알림 발송 (False), 그 외 일시 지연은 진행(True).
    if "만료" in last_reason or "401" in last_reason:
        return False, last_reason
    return True, f"(경고) proxy 응답 지연 — LLM 시도는 계속함: {last_reason[:80]}"


def extract_bid_fields(title: str, body: str) -> dict[str, str]:
    """공고 제목+본문에서 7개 필드 추출. 실패 시 모두 빈 문자열."""
    body = (body or "")[:12000]
    if not title and not body:
        return dict(EMPTY_FIELDS)

    payload: dict[str, Any] = {
        "model": OPENCLAW_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"공고 제목: {title}\n\n공고 본문:\n{body}"},
        ],
        "max_tokens": 800,
        "temperature": 0.2,
    }
    try:
        r = requests.post(OPENCLAW_PROXY_URL, json=payload, timeout=SUMMARIZE_TIMEOUT)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("LLM 요약 실패 (title=%s...): %s", title[:30], exc)
        return dict(EMPTY_FIELDS)

    # openclaw proxy가 LLM 호출 실패 시 응답을 200 + content에 error 메시지로 흘려보냄
    # (예: 401 인증 만료). 명시적으로 잡아서 로그.
    if content and ("401" in content[:80] or "Failed to authenticate" in content[:80] or "authentication_error" in content[:200]):
        logger.warning("openclaw 인증 실패 (Claude Max OAuth 만료 가능) — 토큰 갱신 필요. raw=%r", content[:200])
        return dict(EMPTY_FIELDS)

    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("LLM JSON 파싱 실패. raw=%r", content[:200])
        return dict(EMPTY_FIELDS)

    # 응답 자체가 error 객체인 경우 (proxy가 error JSON을 그대로 흘려보냄)
    if isinstance(parsed, dict) and "error" in parsed and "type" in parsed:
        logger.warning("LLM 에러 응답: %r", parsed)
        return dict(EMPTY_FIELDS)

    result = dict(EMPTY_FIELDS)
    for k in result:
        if k in parsed:
            result[k] = str(parsed[k] or "").strip()
    return result
