from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..utils import parse_date, parse_price
from urllib.parse import quote

from .base import Adapter, Attachment, BidPosting

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
            body, deadline_at, price, attachments = self._maybe_fetch_detail(detail_url)
        else:
            body, deadline_at, price, attachments = "", None, None, []

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
            attachments=attachments,
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

    def _maybe_fetch_detail(self, detail_url: str) -> tuple[str, datetime | None, int | None, list[Attachment]]:
        if not detail_url:
            return "", None, None, []
        try:
            html = self._get(detail_url)
        except RuntimeError as exc:
            logger.debug("[%s] detail fetch failed: %s", self.site.name, exc)
            return "", None, None, []

        soup = BeautifulSoup(html, "lxml")
        # 본문 영역 우선 추출 — 페이지 헤더/네비 제외
        body_container = self._find_body_container(soup)
        if body_container is not None:
            text = body_container.get_text("\n", strip=True)
        else:
            text = soup.get_text("\n", strip=True)
        deadline = self._find_deadline(text)
        price = parse_price(text)
        attachments = self._extract_attachments(soup, detail_url)
        return text[:8000], deadline, price, attachments

    _BODY_SELECTORS: tuple[str, ...] = (
        "div#contents", "div.board_view", "table.bbs_view",
        "div.view_cont", "div.view_contents", "div.board_content",
        "div.template", "div.contentsArea", "div.content_in",
        "div.dl_view", "table.tbl_view", "div.p-wrap",
    )

    def _find_body_container(self, soup: BeautifulSoup) -> Tag | None:
        for sel in self._BODY_SELECTORS:
            el = soup.select_one(sel)
            if isinstance(el, Tag) and len(el.get_text(strip=True)) > 200:
                return el
        return None

    def _extract_attachments(self, soup: BeautifulSoup, detail_url: str) -> list[Attachment]:
        """첨부파일 링크 추출. javascript 다운로드 함수 패턴 자동 처리."""
        results: list[Attachment] = []
        seen: set[str] = set()
        _FILE_EXT = re.compile(r"\.(hwp|hwpx|pdf|docx?|xlsx?|pptx?|zip|jpg|png)\b", re.IGNORECASE)

        for a in soup.find_all("a"):
            href = (a.get("href") or "").strip()
            onclick = (a.get("onclick") or "").strip()
            text = a.get_text(strip=True)
            combined = f"{href} {onclick} {text}"

            ext_match = _FILE_EXT.search(combined)
            if not ext_match and "filedown" not in combined.lower() and "downloadfile" not in combined.lower():
                continue

            file_url, file_name = self._resolve_download(href, onclick, text, detail_url)
            if not file_url or file_name in seen:
                continue
            seen.add(file_name)
            results.append(Attachment(name=file_name, url=file_url))
        return results

    def _resolve_download(self, href: str, onclick: str, text: str, detail_url: str) -> tuple[str, str]:
        """download 링크와 파일명 추정. 알려진 javascript 패턴 + 일반 URL fallback."""
        # 1) href가 절대 또는 상대 URL이면 그대로
        if href.startswith("http"):
            return href, self._filename_from_url_or_text(href, text)
        if href and not href.startswith("javascript:") and href not in ("#", ""):
            from urllib.parse import urljoin
            absolute = urljoin(detail_url, href)
            return absolute, self._filename_from_url_or_text(absolute, text)

        # 2) javascript 패턴별 처리
        combined = f"{href} {onclick}"
        # 수원시·여주시·세종 등 eminwon 패턴: goDownLoad(user_nm, sys_nm, file_path)
        m = re.search(r"goDownLoad\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", combined)
        if m:
            user_nm, sys_nm, path = m.group(1), m.group(2), m.group(3)
            base = self._eminwon_download_base(detail_url)
            return f"{base}?user_file_nm={quote(user_nm)}&sys_file_nm={quote(sys_nm)}&file_path={quote(path)}", user_nm
        # 가평군·김포시 등 ND_fileDownload 패턴
        m = re.search(r"['\"](/[^'\"]*fileDownload[^'\"]*)['\"]", combined, re.IGNORECASE)
        if m:
            path = m.group(1)
            from urllib.parse import urljoin
            return urljoin(self.site.base_url + "/", path), self._guess_filename(text, path)
        # 일반 download/atch 패턴
        m = re.search(r"['\"](/[^'\"]+(?:atch|download)[^'\"]+)['\"]", combined, re.IGNORECASE)
        if m:
            from urllib.parse import urljoin
            return urljoin(self.site.base_url + "/", m.group(1)), self._guess_filename(text, m.group(1))
        return "", ""

    def _eminwon_download_base(self, detail_url: str) -> str:
        """수원시는 www.suwon.go.kr → eminwon.suwon.go.kr 로 다운로드. 추정."""
        from urllib.parse import urlparse
        p = urlparse(detail_url or self.site.base_url)
        host = p.netloc
        if host.startswith("www."):
            host = "eminwon." + host[4:]
        return f"{p.scheme}://{host}/emwp/jsp/ofr/FileDown.jsp"

    @staticmethod
    def _filename_from_url_or_text(url: str, text: str = "") -> str:
        """URL의 user_file_nm 쿼리 파라미터 또는 텍스트에서 파일명 추출 (eminwon 호환)."""
        from urllib.parse import urlparse, parse_qs, unquote
        # 1) URL에 user_file_nm 쿼리가 있으면 가장 정확
        try:
            qs = parse_qs(urlparse(url).query)
            for key in ("user_file_nm", "file_name", "fileName", "fileNm"):
                if qs.get(key):
                    name = unquote(qs[key][0])
                    if "." in name:
                        return name
        except Exception:
            pass
        # 2) 표시 텍스트에서 .ext 패턴 (수원시: "hwp파일명.hwp" 같은 접두사 제거)
        for source in (text, url):
            m = re.search(r"([^/\s'\"]+\.(?:hwp|hwpx|pdf|docx?|xlsx?|pptx?|zip))", source, re.IGNORECASE)
            if m:
                name = m.group(1)
                return re.sub(r"^(hwp|hwpx|pdf|doc|docx|xls|xlsx|ppt|pptx|zip)(?=[가-힣A-Z\(])", "", name)
        return (text or url)[:80]

    @classmethod
    def _guess_filename(cls, text: str, url_or_text: str = "") -> str:
        """후방 호환용 별칭."""
        return cls._filename_from_url_or_text(url_or_text, text)

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
