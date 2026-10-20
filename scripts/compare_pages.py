"""쪽 넘김 전·후 비교 — 게시판 목록만 읽는다 (상세·첨부·DB 쓰기 없음).

'전'(원래 폴더 코드)과 '후'(이 폴더 코드)를 사이트마다 별도 프로세스로 연달아 돌려
notice_id 목록을 비교한다. sites는 DB가 아니라 미리 저장해 둔 JSON 파일에서 읽는다.

실행:
    .venv/bin/python scripts/compare_pages.py --sites ~/sites_enabled_2026-10-20.json \
        --before "/Users/dev06/Safety diagnosis bid" --after . [--only 고양시,화성시]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Any

# 바꿀 곳의 제안 설정 — 제안 SQL과 같은 값. pagination은 DB 값 위에 덮고, list_params도 덮는다
PROPOSED: dict[str, dict[str, dict[str, Any]]] = {
    "의정부시": {"pagination": {"param": "page"}},
    "평택도시공사-입찰정보": {"pagination": {"param": "page"}},
    "평택도시공사-조달청": {"pagination": {"param": "page"}},
    "경기주택도시공사-조달청": {"pagination": {"max_pages": 1}, "list_params": {"articleLimit": "50"}},
    "화성시": {"pagination": {"also": ["q_cp"], "max_pages": 3}},
    "경기도청": {"pagination": {"mode": "click"}},
    "고양시": {"pagination": {"mode": "click"}},
    "부천시": {"pagination": {"mode": "click"}},
    "용인시-건축": {"pagination": {"mode": "click"}},
    "용인시-토목": {"pagination": {"mode": "click"}},
    "의왕시": {"pagination": {"mode": "click"}},
}
SEARCH_KEYS: tuple[str, ...] = ("q_sv", "searchTxt", "searchKrwd", "q_searchVal", "searchWrd")


def apply_proposed(row: dict[str, Any]) -> dict[str, Any]:
    over = PROPOSED.get(row["name"])
    if not over:
        return row
    new = dict(row)
    new["pagination"] = {**(row.get("pagination") or {}), **over.get("pagination", {})}
    new["list_params"] = {**(row.get("list_params") or {}), **over.get("list_params", {})}
    return new


def side_main(root: str, row: dict[str, Any]) -> None:
    """자식 프로세스 — root 폴더의 코드로 목록 쪽들을 읽어 JSON 한 줄로 출력."""
    sys.path.insert(0, root)
    import src.adapters.egov as egov
    import src.adapters.playwright_adapter as pw
    from src.adapters.registry import build_adapter
    from src.config import RuntimeConfig, _site_row_to_config

    site = _site_row_to_config(row)
    ad = build_adapter(site, RuntimeConfig(lookback_hours=720, request_delay_sec=0.3, http_timeout_sec=15))
    pages: list[list[dict[str, str]]] = []
    err = ""
    t0 = time.time()
    for page in range(1, int(site.pagination.get("max_pages", 3)) + 1):
        try:
            if hasattr(egov, "page_request"):
                params, click = egov.page_request(site.pagination, site.list_params, page)
                html = ad._get_list_page(params, click)
            else:  # 옛 코드 — fetch()의 목록 요청과 같은 방식
                params = dict(site.list_params)
                params[str(site.pagination.get("param", "pageIndex"))] = str(page)
                html = ad._get(site.list_url, params=params)
        except Exception as exc:
            err = f"page {page}: {str(exc)[:120]}"
            break
        rows: list[dict[str, str]] = []
        for r in ad._extract_rows(html):
            title, url = ad._extract_title_and_url(r)
            if not title:
                continue
            d = ad._extract_date(r)
            rows.append({"id": ad._infer_notice_id(r, url, title), "title": title,
                         "date": d.date().isoformat() if d else ""})
        if not rows:
            break
        pages.append(rows)
    print(json.dumps({"code": [egov.__file__, pw.__file__], "pages": pages, "err": err,
                      "sec": round(time.time() - t0, 1)}, ensure_ascii=False))


def run_side(root: str, row: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--side", root, "--row", json.dumps(row, ensure_ascii=False)],
        capture_output=True, text=True, timeout=600, cwd=root,
    )
    last = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not last:
        return {"code": ["?", "?"], "pages": [], "err": (proc.stderr.strip().splitlines() or ["no output"])[-1][:150], "sec": 0}
    return json.loads(last[-1])


def dedup(pages: list[list[dict[str, str]]]) -> list[str]:
    return list(dict.fromkeys(r["id"] for p in pages for r in p))


def same_with_shift(a: list[str], b: list[str], k_max: int = 5) -> bool:
    """같은 순서이고 한쪽 맨 위에 새 글이 몇 건 더 있는 정도면 같음."""
    if a == b:
        return True
    for k in range(1, k_max + 1):
        if b[k:] == a[: len(b) - k] or a[k:] == b[: len(a) - k]:
            return True
    return False


def judge_fixed(name: str, row: dict[str, Any], after: dict[str, Any]) -> tuple[str, str]:
    pages = after["pages"]
    search = any(k in (row.get("list_params") or {}) for k in SEARCH_KEYS) or bool((row.get("selectors") or {}).get("search_keyword"))
    if int(row["pagination"].get("max_pages", 3)) == 1:
        total = len(dedup(pages))
        ok = total >= 30 or search
        return ("합격" if ok else "불합격"), f"한 번에 서로 다른 글 {total}건"
    if len(pages) < 2:
        # 검색 목록이 한 쪽에 다 들어가면 2쪽 링크 자체가 없다 — 결과 전부를 읽은 것
        ok = search and bool(pages)
        return ("합격" if ok else "불합격"), f"2쪽 없음{' — 검색 결과 ' + str(len(dedup(pages))) + '건이 한 쪽에 다 들어감' if ok else ''}"
    p1, p2 = pages[0], pages[1]
    ids1 = {r["id"] for r in p1}
    # 모든 쪽에 되풀이되는 행 = 상단 고정 공지나 쪽 넘김 잡행(예: 'title=10'). 글 수·겹침에서 뺀다
    repeated = set.intersection(*({r["id"] for r in p} for p in pages))
    other_overlap = sum(1 for r in p2 if r["id"] in ids1 and r["id"] not in repeated)
    body2 = [r for r in p2 if r["id"] not in repeated]
    first2 = next((r["date"] for r in body2 if r["date"]), "")
    last1 = next((r["date"] for r in reversed(p1) if r["date"] and r["id"] not in repeated), "")
    date_ok = not (first2 and last1) or first2 <= last1
    # 날짜 있는 반복행은 상단 고정 '글'이라 센다. 날짜 없는 잡행(예: 'title=10')만 뺀다
    junk = {r["id"] for p in pages for r in p if r["id"] in repeated and not r["date"]}
    total = len([i for i in dedup(pages) if i not in junk])
    enough = total >= 30 or (search and len(pages[-1]) < len(p1))
    ok = other_overlap == 0 and date_ok and enough and bool(body2)
    titles = " / ".join(r["title"][:28] for r in body2[:5])
    rep_titles = ",".join(sorted({r["title"][:12] for p in pages for r in p if r["id"] in repeated}))
    note = (f"반복행 {len(repeated)}({rep_titles}) · 반복 외 겹침 {other_overlap} · 2쪽첫 {first2 or '-'} ≤ 1쪽끝 {last1 or '-'} "
            f"{'O' if date_ok else '✗'} · 잡행 뺀 합계 {total}{' (검색)' if search else ''} · 2쪽: {titles}")
    return ("합격" if ok else "불합격"), note


def title_collisions(name: str, after: dict[str, Any]) -> list[str]:
    """notice_id가 제목으로 만들어지는 곳 — 1~3쪽에서 id가 같은데 다른 글인 쌍."""
    seen: dict[str, list[tuple[int, str, str]]] = {}
    for n, p in enumerate(after["pages"], 1):
        for r in p:
            if r["id"].startswith("title="):
                seen.setdefault(r["id"], []).append((n, r["title"], r["date"]))
    out: list[str] = []
    for i, v in seen.items():
        if len(v) < 2 or len(v) == len(after["pages"]) == len({n for n, _, _ in v}) > 1 and len({(t, d) for _, t, d in v}) == 1:
            continue  # 한 번만 나오거나, 모든 쪽에 똑같이 되풀이되는 행(고정 공지·잡행)
        out.append(f"{name} | {i} | " + " ↔ ".join(f"{n}쪽 {t} ({d or '날짜없음'})" for n, t, d in v))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side")
    ap.add_argument("--row")
    ap.add_argument("--sites")
    ap.add_argument("--before")
    ap.add_argument("--after")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    if a.side:
        side_main(a.side, json.loads(a.row))
        return

    before_root = os.path.abspath(os.path.expanduser(a.before))
    after_root = os.path.abspath(os.path.expanduser(a.after))
    rows = json.load(open(os.path.expanduser(a.sites), encoding="utf-8"))
    only = [n for n in a.only.split(",") if n]
    if only:
        rows = [r for r in rows if r["name"] in only]

    counts: dict[str, int] = {}
    collisions: list[str] = []
    printed_code = False
    print("이름 | 구분 | 전 쪽별 글 수 | 후 쪽별 글 수 | 서로 다른 글 전→후 | 시간(초) 전→후 | 판정 | 비고")
    for row in rows:
        name = row["name"]
        new_row = apply_proposed(row)
        before = run_side(before_root, row)
        after = run_side(after_root, new_row)
        if not printed_code:
            print(f"CODE 전: {before['code']}\nCODE 후: {after['code']}")
            printed_code = True
        valid = before["code"][0].startswith(before_root) and after["code"][0].startswith(after_root)
        kind = "고친 곳" if name in PROPOSED else "그대로"
        b_ids, a_ids = dedup(before["pages"]), dedup(after["pages"])
        if not valid:
            verdict, note = "무효", f"코드 경로 불일치 {before['code'][0]} / {after['code'][0]}"
        elif not before["pages"] and not after["pages"]:
            verdict, note = "양쪽 실패", after["err"] or before["err"]
        elif name in PROPOSED:
            verdict, note = judge_fixed(name, new_row, after)
        elif same_with_shift(b_ids, a_ids):
            verdict, note = "같음", ""
        else:
            # 한 번 더 돌려 다시 나올 때만 회귀
            before2, after2 = run_side(before_root, row), run_side(after_root, new_row)
            again = not same_with_shift(dedup(before2["pages"]), dedup(after2["pages"]))
            verdict = "회귀" if again else "같음(재실행)"
            note = f"1차 차이: 전 {len(b_ids)} / 후 {len(a_ids)}"
        counts[verdict] = counts.get(verdict, 0) + 1
        collisions += title_collisions(name, after)
        bp = "+".join(str(len(p)) for p in before["pages"]) or "0"
        apg = "+".join(str(len(p)) for p in after["pages"]) or "0"
        err = f" [오류 전:{before['err']} 후:{after['err']}]" if (before["err"] or after["err"]) and verdict != "양쪽 실패" else ""
        print(f"{name} | {kind} | {bp} | {apg} | {len(b_ids)}→{len(a_ids)} | {before['sec']}→{after['sec']} | {verdict} | {note}{err}", flush=True)
        time.sleep(1)
    print("집계:", counts)
    print("② 제목 notice_id 겹침 (후, 1~3쪽):")
    for c in collisions or ["(없음)"]:
        print("  " + c)


if __name__ == "__main__":
    main()
