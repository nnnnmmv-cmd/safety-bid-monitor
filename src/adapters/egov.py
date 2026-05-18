from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..utils import parse_date, parse_price
from .base import Adapter, BidPosting

logger: logging.Logger = logging.getLogger(__name__)

_DEFAULT_ROW_SELECTORS: list[str] = [
    "table.board_list tbody tr",
    "table.board-list tbody tr",
    "table.bbs_list tbody tr",
    "table.tbl_list tbody tr",
    "table.list tbody tr",
    "table.brd_list tbody tr",
    "table.tbl_board tbody tr",
    "table.bbs_default_list tbody tr",
    "table.p-table tbody tr",
    "table tbody tr",
    "table#boardList tr",
    "table.tb_basic tr",
    "tbody.list tr",
    "ul.board_list li",
    "div.board_list ul li",
]

_DATE_CELL_HINTS: tuple[str, ...] = ("date", "regdate", "wdate", "reg", "day", "일자", "등록일")

# javascript fnGoDetail(N) → detail URL 자동 변환 룰
# 안산시·하남시·군포시·수원시·김포시·여주시 등 흔한 eGov-기반 패턴 정리
_DETAIL_URL_RULES: list[dict[str, str]] = [
    # 안산시: selectPageListBbs.do?... → selectBbsDetail.do?bbs_seq=N
    {"list": "selectPageListBbs", "detail": "selectBbsDetail", "seq_key": "bbs_seq"},
    # 군포시 등 eminwon notice 패턴
    {"list": "selectEminwonNoticeList", "detail": "selectEminwonNoticeView", "seq_key": "not_ancmt_mgt_no"},
    # 여주시·세종 등 eminwon 일반
    {"list": "selectEminwonList", "detail": "selectEminwonView", "seq_key": "not_ancmt_mgt_no"},
    # 가평군·하남시 등 고시 패턴
    {"list": "selectGosiList", "detail": "selectGosiData", "seq_key": "not_ancmt_mgt_no"},
    # 김포시 등
    {"list": "ntfcPblancList", "detail": "ntfcPblancView", "seq_key": "not_ancmt_mgt_no"},
    # 수원시 ofr 패턴
    {"list": "BD_ofrList", "detail": "BD_ofrView", "seq_key": "notAncmtMgtNo"},
    # 일반 List.do → View.do (fallback)
    {"list": "List.do", "detail": "View.do", "seq_key": "seq"},
    {"list": "list.do", "detail": "view.do", "seq_key": "seq"},
]


def _convert_list_to_detail_url(list_url: str, list_params: dict[str, str], seq: str) -> str:
    """list 게시판 URL + 글번호 → detail URL 자동 추정. 매칭 룰 없으면 빈 문자열."""
    for rule in _DETAIL_URL_RULES:
        if rule["list"] in list_url:
            new_url = list_url.replace(rule["list"], rule["detail"])
            params = {k: v for k, v in list_params.items() if v}
            params[rule["seq_key"]] = seq
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{new_url}?{qs}" if qs else new_url
    return ""


class EgovAdapter(Adapter):
    """eGovFrame 표준 게시판(BBSMSTR_XXX 패턴) 및 일반 테이블형 게시판 대응."""

    def fetch(self, since: datetime) -> list[BidPosting]:
        max_pages = int(self.site.pagination.get("max_pages", 3))
        page_param = str(self.site.pagination.get("param", "pageIndex"))
        results: list[BidPosting] = []

        for page in range(1, max_pages + 1):
            params = dict(self.site.list_params)
            params[page_param] = str(page)
            try:
                html = self._get(self.site.list_url, params=params)
            except RuntimeError as exc:
                logger.warning("[%s] list fetch failed page=%d: %s", self.site.name, page, exc)
                break

            rows = self._extract_rows(html)
            if not rows:
                logger.info("[%s] no rows parsed on page %d", self.site.name, page)
                break

            stop = False
            for row in rows:
                posting = self._row_to_posting(row, html)
                if posting is None:
                    continue
                if posting.posted_at and posting.posted_at < since:
                    stop = True
                    continue
                results.append(posting)
            if stop:
                break

        logger.info("[%s] fetched %d postings", self.site.name, len(results))
        return results

    def _extract_rows(self, html: str) -> list[Tag]:
        soup = BeautifulSoup(html, "lxml")
        custom = self.site.selectors.get("row")
        candidates: list[str] = [custom] if custom else list(_DEFAULT_ROW_SELECTORS)
        for sel in candidates:
            rows = [r for r in soup.select(sel) if isinstance(r, Tag) and r.find(["a", "td"])]
            if rows:
                return rows
        return []

    def _row_to_posting(self, row: Tag, raw_html: str) -> BidPosting | None:
        title, detail_url = self._extract_title_and_url(row)
        if not title:
            return None

        posted_at = self._extract_date(row)
        notice_id = self._infer_notice_id(row, detail_url, title)

        # title 사전 매칭으로 detail fetch 절감 (99% 절감 가능)
        should_fetch_detail = True
        if self.prefilter_titles:
            should_fetch_detail = any(kw in title for kw in self.prefilter_titles)

        if should_fetch_detail:
            body, deadline_at, price = self._maybe_fetch_detail(detail_url)
        else:
            body, deadline_at, price = "", None, None

        return BidPosting(
            notice_id=self._make_notice_id(notice_id),
            site_name=self.site.name,
            title=title,
            org=self.site.name,
            posted_at=posted_at,
            deadline_at=deadline_at,
            url=detail_url,
            estimated_price=price,
            body=body,
            raw_html=str(row),
            region=self.site.region,
        )

    def _extract_title_and_url(self, row: Tag) -> tuple[str, str]:
        title_sel = self.site.selectors.get("title")
        link: Tag | None = None
        if title_sel:
            found = row.select_one(title_sel)
            link = found if isinstance(found, Tag) else None
        if link is None:
            link = row.find("a")
        if not isinstance(link, Tag):
            return "", ""

        title = link.get_text(strip=True)
        href_attr = self.site.selectors.get("detail_url_attr", "href")
        href_value = link.get(href_attr) or link.get("href") or ""
        href = str(href_value).strip()
        onclick = str(link.get("onclick") or "").strip()

        # href가 비었거나 '#' 또는 javascript:면 onclick까지 합쳐서 URL/숫자 추출
        if not href or href in ("#",) or href.startswith("javascript:") or "javascript:" in href:
            combined = f"{href} {onclick}".strip()
            m = re.search(r"['\"]([^'\"]+)['\"]|\((\s*\d+\s*)\)", combined)
            token = ""
            if m:
                token = (m.group(1) or m.group(2) or "").strip()
            if token.startswith("http") or token.startswith("/"):
                href = token
            elif token.isdigit() and self.site.list_url:
                # 글번호만 추출됨 → list URL 패턴을 detail URL 패턴으로 자동 변환 시도
                href = _convert_list_to_detail_url(self.site.list_url, self.site.list_params, token)
            else:
                href = ""
        if href and not href.startswith("http"):
            # list_url(있으면) 기준으로 합쳐서 상대경로 ./xxx 가 정확히 디렉토리 기준이 되게
            base_for_join = self.site.list_url or (self.site.base_url + "/")
            href = urljoin(base_for_join, href)
        # 상세 URL 추출 실패 시 list_url(+검색 파라미터)로 폴백 — 사용자가 게시판에서 직접 찾을 수 있게
        if not href:
            base = self.site.list_url or self.site.base_url
            if self.site.list_params and "?" not in base:
                qs = "&".join(f"{k}={v}" for k, v in self.site.list_params.items() if v)
                if qs:
                    base = f"{base}?{qs}"
            href = base
        return title, href

    def _extract_date(self, row: Tag) -> datetime | None:
        date_sel = self.site.selectors.get("date")
        if date_sel:
            cell = row.select_one(date_sel)
            if isinstance(cell, Tag):
                return parse_date(cell.get_text(strip=True))

        for cell in row.find_all(["td", "span", "div", "li"]):
            classes = " ".join(cell.get("class") or []).lower()
            if any(hint in classes for hint in _DATE_CELL_HINTS):
                parsed = parse_date(cell.get_text(strip=True))
                if parsed:
                    return parsed

        for cell in row.find_all(["td", "span", "div", "li"]):
            parsed = parse_date(cell.get_text(strip=True))
            if parsed:
                return parsed
        return None

    def _infer_notice_id(self, row: Tag, detail_url: str, title: str) -> str:
        for key in ("nttId", "ntt_id", "no", "idx", "seq"):
            m = re.search(rf"{key}=([0-9]+)", detail_url)
            if m:
                return f"{key}={m.group(1)}"
        no_cell = row.find("td")
        if isinstance(no_cell, Tag):
            text = no_cell.get_text(strip=True)
            if text.isdigit():
                return f"row={text}"
        return f"title={title[:40]}"

    def _maybe_fetch_detail(self, detail_url: str) -> tuple[str, datetime | None, int | None]:
        if not detail_url:
            return "", None, None
        try:
            html = self._get(detail_url)
        except RuntimeError as exc:
            logger.debug("[%s] detail fetch failed: %s", self.site.name, exc)
            return "", None, None

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text("\n", strip=True)
        deadline = self._find_deadline(text)
        price = parse_price(text)
        return text[:5000], deadline, price

    def _find_deadline(self, text: str) -> datetime | None:
        for keyword in ("입찰마감", "접수마감", "제출마감", "신청마감", "마감일시", "마감일"):
            idx = text.find(keyword)
            if idx == -1:
                continue
            window = text[idx : idx + 80]
            parsed = parse_date(window)
            if parsed:
                return parsed
        return None
