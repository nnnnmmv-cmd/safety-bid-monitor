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


def extract_bid_fields(title: str, body: str) -> dict[str, str]:
    """공고 제목+본문에서 7개 필드 추출. 실패 시 모두 빈 문자열."""
    body = (body or "")[:4000]
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

    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("LLM JSON 파싱 실패. raw=%r", content[:200])
        return dict(EMPTY_FIELDS)

    result = dict(EMPTY_FIELDS)
    for k in result:
        if k in parsed:
            result[k] = str(parsed[k] or "").strip()
    return result
