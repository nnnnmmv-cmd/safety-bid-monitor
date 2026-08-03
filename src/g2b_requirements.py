"""나라장터 공고문에서 참가자격 요건을 추출해 허브 arch_bid_notices에 기록.

- 대상: relevance='likely' AND extract_status IS NULL AND spec_docs IS NOT NULL AND 마감 전
- 첨부는 PDF 우선(추출 안정성), 없으면 HWP/HWPX (기존 attachments.py 경로 재사용)
- LLM은 원문 인용 위주로만 뽑는다 — 참여 가능/불가 판정은 허브(윈도우 세션) 몫
- 쓰는 칸: requirements / extract_status / extracted_at (그 외 칸 수정 금지)
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import attachments as att_mod
from . import summarizer

logger: logging.Logger = logging.getLogger("safetybid.g2b")

HUB_ENV: Path = Path.home() / "homecheck-sales-hub" / ".env.local"
MAX_PER_CYCLE: int = 15
WORK_ROOT: Path = Path("/tmp/g2b_specs")
# 공고문 본문이 길어 앞부분만으로도 자격요건 파악 가능. 프록시 토큰 한도 고려.
MAX_DOC_CHARS: int = 14000

SYSTEM_PROMPT: str = """너는 대한민국 공공 입찰 공고문에서 '참가자격 요건'만 그대로 뽑아내는 도구다.

규칙:
- 공고문에 적힌 표현을 최대한 원문 그대로 인용한다. 해석·요약·추론하지 마라.
- 공고문에 없는 내용은 절대 지어내지 마라. 해당 항목이 없으면 null.
- licenses는 배열, 나머지는 문자열 또는 null.
- 설명 문장 없이 JSON만 출력한다.

출력 형식:
{"licenses":["요구 면허·등록 원문 그대로, 항목별"],"region":"본점 소재지 요건 원문 (없으면 null)","experience":"실적 요건 원문 (없으면 null)","engineer":"책임기술자 요건 원문 (없으면 null)","scoring":"낙찰자 결정·배점 방식 원문 요약 (없으면 null)","small_biz":"소기업·소상공인 등 기업규모 제한 원문 (없으면 null)"}"""

_FIELDS: tuple[str, ...] = ("licenses", "region", "experience", "engineer", "scoring", "small_biz")


def _hub_client():
    from dotenv import dotenv_values
    from supabase import create_client

    env = dotenv_values(HUB_ENV)
    url = (env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not (url and key):
        raise RuntimeError(f"허브 접속값 없음: {HUB_ENV}")
    return create_client(url, key)


def _pick_doc(spec_docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """PDF 우선, 없으면 HWP/HWPX. 과업지시서보다 '공고문'을 우선한다."""
    docs = [d for d in (spec_docs or []) if isinstance(d, dict) and d.get("url")]
    if not docs:
        return None

    def rank(d: dict[str, Any]) -> tuple[int, int]:
        name = (d.get("name") or "").lower()
        ext_score = 0 if name.endswith(".pdf") else (1 if name.endswith((".hwp", ".hwpx")) else 2)
        # 공고문에 자격요건이 있고 과업지시서엔 기술 사양이 주로 있음
        name_score = 0 if "공고" in name else 1
        return (ext_score, name_score)

    return sorted(docs, key=rank)[0]


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = m.group(1) if m else text
    if not m:
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e <= s:
            return None
        raw = raw[s : e + 1]
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _call_llm(title: str, doc_text: str) -> dict[str, Any] | None:
    payload = {
        "model": summarizer.OPENCLAW_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"공고명: {title}\n\n공고문 본문:\n{doc_text[:MAX_DOC_CHARS]}"},
        ],
        "max_tokens": 1200,
        "temperature": 0.1,
    }
    try:
        r = requests.post(summarizer.OPENCLAW_PROXY_URL, json=payload, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("[g2b] LLM 호출 실패: %s", exc)
        return None

    if content and ("authentication_error" in content[:200] or "Failed to authenticate" in content[:80]):
        logger.warning("[g2b] LLM 인증 만료 — 이번 사이클 중단")
        return None

    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("[g2b] JSON 파싱 실패. raw=%r", (content or "")[:160])
        return None

    # 스키마 고정 — 없는 키는 null, licenses는 항상 배열
    out: dict[str, Any] = {}
    for k in _FIELDS:
        v = parsed.get(k)
        if k == "licenses":
            if isinstance(v, str):
                v = [v] if v.strip() else []
            out[k] = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
        else:
            out[k] = str(v).strip() if v not in (None, "", "null") else None
    return out


def _doc_text(doc: dict[str, Any], work: Path) -> str:
    """첨부 1개 다운로드 → 텍스트. 실패 시 빈 문자열.
    HTML 에러페이지 차단·확장자 오기 보정은 attachments.py가 이미 처리."""
    src = att_mod.download_attachment(doc.get("url", ""), work, doc.get("name") or "spec.bin")
    if src is None or not src.exists():
        return ""
    return att_mod.extract_attachment_text(src)


def _fetch_targets(client, limit: int) -> list[dict[str, Any]]:
    now_iso = datetime.now(timezone.utc).isoformat()
    res = (
        client.table("arch_bid_notices")
        .select("bid_ntce_no, bid_ntce_ord, title, spec_docs")
        .eq("relevance", "likely")
        .is_("extract_status", "null")
        .not_.is_("spec_docs", "null")
        .gte("bid_clse_dt", now_iso)
        .order("bid_clse_dt")
        .limit(limit)
        .execute()
    )
    return res.data or []


def _write_result(client, no: str, ord_: str, status: str, reqs: dict[str, Any] | None) -> None:
    patch: dict[str, Any] = {
        "extract_status": status,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    if reqs is not None:
        patch["requirements"] = reqs
    client.table("arch_bid_notices").update(patch).eq("bid_ntce_no", no).eq("bid_ntce_ord", ord_).execute()


def run(limit: int = MAX_PER_CYCLE) -> dict[str, int]:
    """사이클당 1회 호출. 반환: 상태별 건수."""
    client = _hub_client()
    targets = _fetch_targets(client, limit)
    stats = {"done": 0, "no_doc": 0, "failed": 0}
    if not targets:
        logger.info("[g2b] 추출 대상 없음")
        return stats

    logger.info("[g2b] 추출 대상 %d건", len(targets))
    WORK_ROOT.mkdir(parents=True, exist_ok=True)

    for t in targets:
        no, ord_ = t["bid_ntce_no"], t["bid_ntce_ord"]
        title = t.get("title") or ""
        work = WORK_ROOT / re.sub(r"[^\w-]", "_", f"{no}_{ord_}")
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            doc = _pick_doc(t.get("spec_docs") or [])
            text = _doc_text(doc, work) if doc else ""
            # 1순위 첨부가 실패하면 나머지 첨부로 폴백
            if len(text) < 200:
                for alt in (t.get("spec_docs") or []):
                    if doc and alt.get("url") == doc.get("url"):
                        continue
                    text = _doc_text(alt, work)
                    if len(text) >= 200:
                        break
            if len(text) < 200:
                _write_result(client, no, ord_, "no_doc", None)
                stats["no_doc"] += 1
                logger.info("[g2b] no_doc: %s", title[:40])
                continue

            reqs = _call_llm(title, text)
            if reqs is None:
                _write_result(client, no, ord_, "failed", None)
                stats["failed"] += 1
                logger.info("[g2b] failed: %s", title[:40])
                continue

            _write_result(client, no, ord_, "done", reqs)
            stats["done"] += 1
            logger.info("[g2b] done: %s (면허 %d건)", title[:40], len(reqs.get("licenses") or []))
        except Exception as exc:
            logger.warning("[g2b] 처리 중 예외 (%s): %s", title[:35], exc)
            try:
                _write_result(client, no, ord_, "failed", None)
                stats["failed"] += 1
            except Exception:
                pass
        finally:
            shutil.rmtree(work, ignore_errors=True)

    logger.info("[g2b] 완료 — done=%d no_doc=%d failed=%d", stats["done"], stats["no_doc"], stats["failed"])
    return stats


class _FakeClient:
    """selftest용 — update 패치 내용만 기록하는 최소 스텁."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.patches: list[dict[str, Any]] = []
        self._pending: dict[str, Any] | None = None

    def table(self, _name: str):
        return self

    # 조회 체인 — 전부 self 반환 후 execute에서 rows
    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def is_(self, *_a, **_k):
        return self

    def gte(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    @property
    def not_(self):
        return self

    def update(self, patch: dict[str, Any]):
        self._pending = patch
        return self

    def execute(self):
        if self._pending is not None:
            self.patches.append(self._pending)
            self._pending = None
            return type("R", (), {"data": []})()
        return type("R", (), {"data": self._rows})()


def _selftest() -> None:
    """JSON 파싱 / no_doc / failed 3개 경로 각 1케이스 (네트워크·DB 없이)."""
    # 1) JSON 파싱
    assert _extract_json('```json\n{"licenses":["안전진단전문기관"]}\n```') == {"licenses": ["안전진단전문기관"]}
    assert _extract_json('설명\n{"region":"서울"}\n끝')["region"] == "서울"
    assert _extract_json("JSON 아님") is None

    # 문서 선택 우선순위
    docs = [
        {"url": "u1", "name": "과업지시서.hwp"},
        {"url": "u2", "name": "공고문.pdf"},
        {"url": "u3", "name": "공고문.hwpx"},
    ]
    assert _pick_doc(docs)["url"] == "u2", "PDF 공고문 우선"
    assert _pick_doc([{"url": "u", "name": "과업.hwp"}])["url"] == "u"
    assert _pick_doc([]) is None

    g = globals()
    row = [{"bid_ntce_no": "T1", "bid_ntce_ord": "000", "title": "테스트공고",
            "spec_docs": [{"url": "http://invalid.test/x.pdf", "name": "공고문.pdf"}]}]
    orig_client, orig_doc, orig_llm = g["_hub_client"], g["_doc_text"], g["_call_llm"]
    try:
        # 2) no_doc — 첨부 텍스트가 안 나오는 경우
        fake = _FakeClient(row)
        g["_hub_client"] = lambda: fake
        g["_doc_text"] = lambda *_a, **_k: ""
        assert run(limit=1) == {"done": 0, "no_doc": 1, "failed": 0}
        assert fake.patches[0]["extract_status"] == "no_doc"
        assert "requirements" not in fake.patches[0], "no_doc는 requirements를 건드리지 않아야 함"

        # 3) failed — 텍스트는 있으나 LLM이 JSON을 못 준 경우
        fake = _FakeClient(row)
        g["_hub_client"] = lambda: fake
        g["_doc_text"] = lambda *_a, **_k: "공" * 500
        g["_call_llm"] = lambda *_a, **_k: None
        assert run(limit=1) == {"done": 0, "no_doc": 0, "failed": 1}
        assert fake.patches[0]["extract_status"] == "failed"

        # done — 정상 경로
        fake = _FakeClient(row)
        g["_hub_client"] = lambda: fake
        g["_call_llm"] = lambda *_a, **_k: {k: ([] if k == "licenses" else None) for k in _FIELDS}
        assert run(limit=1) == {"done": 1, "no_doc": 0, "failed": 0}
        assert fake.patches[0]["extract_status"] == "done"
        assert "requirements" in fake.patches[0]
    finally:
        g["_hub_client"], g["_doc_text"], g["_call_llm"] = orig_client, orig_doc, orig_llm

    print("selftest OK — JSON 파싱 / no_doc / failed / done 4경로 통과")


if __name__ == "__main__":
    _selftest()
