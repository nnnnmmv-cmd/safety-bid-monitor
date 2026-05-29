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
    # 의정부·안성 등 saeol/gosiList → gosiView 패턴 (notAncmtMgtNo 키)
    {"list": "gosiList.do", "detail": "gosiView.do", "seq_key": "notAncmtMgtNo"},
    # 김포시 등
    {"list": "ntfcPblancList", "detail": "ntfcPblancView", "seq_key": "not_ancmt_mgt_no"},
    # 수원시 ofr 패턴
    {"list": "BD_ofrList", "detail": "BD_ofrView", "seq_key": "notAncmtMgtNo"},
    # 조달청 pps.go.kr — seq_key가 bbsSn (기본 List.do→View.do 룰은 seq라 잘못됨 → 더 구체적 룰을 앞에)
    {"list": "kor/bbs/list.do", "detail": "kor/bbs/view.do", "seq_key": "bbsSn"},
    # 인천광역시 조달청 — list_params의 command=searchList → command=searchDetail
    # viewData('sno', 'gosiGbn') 두 인자 모두 필요. seq_key=sno + extra=gosiGbn(토큰 index 1)
    {"list": "command=searchList", "detail": "command=searchDetail", "seq_key": "sno",
     "extra_keys": [("gosiGbn", 1)]},
    # 일반 List.do → View.do (fallback)
    {"list": "List.do", "detail": "View.do", "seq_key": "seq"},
    {"list": "list.do", "detail": "view.do", "seq_key": "seq"},
]


def _convert_list_to_detail_url(list_url: str, list_params: dict[str, str], tokens: list[str] | str) -> str:
    """list 게시판 URL + onclick 토큰들 → detail URL 자동 추정. 매칭 룰 없으면 빈 문자열.

    tokens: onclick의 따옴표 인자들 (예: ['66441', 'A']). 문자열 받으면 [str]로 wrapping.
    seq_key는 tokens[0]에 매핑. extra_keys=[(param_name, token_index)]가 있으면 추가 매핑.
    매칭 대상: ① list_url 문자열 자체, ② list_params의 key=value (예: command=searchList).
    """
    if isinstance(tokens, str):
        tokens = [tokens]
    if not tokens:
        return ""
    # seq는 숫자 토큰 중 가장 긴 것 우선 — 파주시 jsView('1022', '20260527131222297', 'N', 'Y')처럼
    # 첫 인자가 게시판 ID(bbsCd)이고 두 번째가 진짜 글 ID인 케이스 대응.
    # tokens 순서는 보존 (extra_keys 인덱스 호환).
    digits = [t for t in tokens if t.isdigit()]
    seq = max(digits, key=len) if digits else tokens[0]

    def _apply_extras(params: dict[str, str], rule: dict) -> None:
        for key, idx in rule.get("extra_keys", []):
            if 0 <= idx < len(tokens) and tokens[idx]:
                params[key] = tokens[idx]

    for rule in _DETAIL_URL_RULES:
        list_pat = rule["list"]
        # 1) list_url 문자열에 매칭
        if list_pat in list_url:
            new_url = list_url.replace(list_pat, rule["detail"])
            params = {k: v for k, v in list_params.items() if v}
            params[rule["seq_key"]] = seq
            _apply_extras(params, rule)
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            return f"{new_url}?{qs}" if qs else new_url
        # 2) list_params에 key=value 형태로 매칭 (인천 조달청처럼 method가 params에 있는 경우)
        if "=" in list_pat:
            k, _, v = list_pat.partition("=")
            if list_params.get(k) == v:
                dk, _, dv = rule["detail"].partition("=")
                params = {kk: vv for kk, vv in list_params.items() if vv}
                params[dk] = dv
                params[rule["seq_key"]] = seq
                _apply_extras(params, rule)
                qs = "&".join(f"{kk}={vv}" for kk, vv in params.items())
                sep = "&" if "?" in list_url else "?"
                return f"{list_url}{sep}{qs}" if qs else list_url
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
            # 헤더(th-only) / 빈 행 제외 — td가 있고 텍스트 5자 이상
            rows = [
                r for r in soup.select(sel)
                if isinstance(r, Tag)
                and r.find("td") is not None
                and len(r.get_text(strip=True)) >= 5
            ]
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

        # 후보 우선순위: ① custom selector → ② 텍스트 가장 긴 <a> → ③ 텍스트 가장 긴 <td>
        # eminwon(yongin)·일부 사이트는 <a> 없이 <button> 또는 <tr>/<td> onclick으로 동작.
        link: Tag | None = None
        if title_sel:
            found = row.select_one(title_sel)
            if isinstance(found, Tag) and found.get_text(strip=True):
                link = found
        if link is None:
            a_list = [a for a in row.find_all("a") if a.get_text(strip=True)]
            if a_list:
                link = max(a_list, key=lambda a: len(a.get_text(strip=True)))

        # link 없으면 텍스트 가장 긴 td로 폴백 (제목 text 추출 + onclick 수집용)
        text_holder: Tag | None = link
        if text_holder is None:
            td_list = [td for td in row.find_all("td") if td.get_text(strip=True)]
            if td_list:
                text_holder = max(td_list, key=lambda t: len(t.get_text(strip=True)))

        if not isinstance(text_holder, Tag):
            return "", ""

        title = text_holder.get_text(strip=True)
        if not title:
            logger.debug("[%s] title 빈값. 행 일부: %s", self.site.name, str(row)[:300])
            return "", ""

        href_attr = self.site.selectors.get("detail_url_attr", "href")

        # href 수집: data-action 우선 (오산·평택 saeol eGov 패턴, 용인 eminwon td 박기 포함)
        # → href → custom 속성
        href_value: str = ""
        if isinstance(link, Tag):
            href_value = str(
                link.get("data-action")
                or link.get(href_attr)
                or link.get("href")
                or ""
            )
        if not href_value and text_holder is not None:
            # text_holder 자체에 data-action이 박혀있는 경우 (용인시 eminwon td 패턴)
            href_value = str(
                text_holder.get("data-action")
                or text_holder.get(href_attr)
                or text_holder.get("href")
                or ""
            )
        if not href_value and text_holder is not None:
            # 그래도 없으면 자식 a/button 검사
            for el in text_holder.find_all(["a", "button"]):
                v = el.get("data-action") or el.get(href_attr) or el.get("href") or ""
                if v:
                    href_value = str(v)
                    break

        # onclick 수집: link → text_holder → text_holder의 자식 모두 → row
        onclick_parts: list[str] = []
        if isinstance(link, Tag):
            onclick_parts.append(str(link.get("onclick") or ""))
        if isinstance(text_holder, Tag) and text_holder is not link:
            onclick_parts.append(str(text_holder.get("onclick") or ""))
            for child in text_holder.find_all(True):
                v = child.get("onclick")
                if v:
                    onclick_parts.append(str(v))
        onclick_parts.append(str(row.get("onclick") or ""))
        onclick = " ".join(p for p in onclick_parts if p)

        href = href_value.strip()

        # href가 비었거나 fragment(#, #none 등) 또는 javascript:면 onclick까지 합쳐서 URL/숫자 추출
        if not href or href.startswith("#") or href.startswith("javascript:") or "javascript:" in href:
            combined = f"{href} {onclick}".strip()
            # 따옴표 안 인자들 모두 추출 (예: viewData('66441','A') → ['66441','A'])
            tokens = re.findall(r"['\"]([^'\"]+)['\"]", combined)
            if not tokens:
                m = re.search(r"\(\s*(\d+)\s*\)", combined)
                if m:
                    tokens = [m.group(1)]
            first = tokens[0] if tokens else ""
            if first.startswith("http") or first.startswith("/"):
                href = first
            elif first.isdigit() and self.site.list_url:
                # 글번호 + 추가 인자들 → list URL 패턴을 detail URL 패턴으로 자동 변환
                href = _convert_list_to_detail_url(self.site.list_url, self.site.list_params, tokens)
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
        # detail URL 변환 실패 후 list URL fallback인 경우 — 같은 페이지 재요청 방지
        # (Playwright 사이트는 Chromium 재기동 비용이 커서 특히 중요)
        if self.site.list_url and detail_url.startswith(self.site.list_url):
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
        # 파주시 등 — article-body / bbs-cont / view-cont 류
        "div.article-body", "div.article-content", "article.article",
        "div.bbs-cont", "div.bbs_cont", "div.view-cont", "div.cont-view",
    )

    def _find_body_container(self, soup: BeautifulSoup) -> Tag | None:
        # 1차: 200자 이상 (네비/푸터 끼어든 큰 컨테이너 회피)
        for sel in self._BODY_SELECTORS:
            el = soup.select_one(sel)
            if isinstance(el, Tag) and len(el.get_text(strip=True)) > 200:
                return el
        # 2차: 60자 이상 (파주시처럼 핵심 공고문이 짧은 케이스 — 본문 selector 매칭만 되면 채택)
        for sel in self._BODY_SELECTORS:
            el = soup.select_one(sel)
            if isinstance(el, Tag) and len(el.get_text(strip=True)) > 60:
                return el
        return None

    def _extract_attachments(self, soup: BeautifulSoup, detail_url: str) -> list[Attachment]:
        """첨부파일 링크 추출. javascript 다운로드 함수 패턴 자동 처리."""
        results: list[Attachment] = []
        seen: set[str] = set()
        # 이미지 확장자(jpg/png/gif) 제외 — 본문 인라인 이미지(홈 마크 등)가 첨부로 잡히는 노이즈 차단.
        _FILE_EXT = re.compile(r"\.(hwp|hwpx|pdf|docx?|xlsx?|pptx?|zip)\b", re.IGNORECASE)
        # Synap·OnlineViewer·웹문서뷰어는 HTML 미리보기 페이지 — 파일 아님. 제외.
        # ckeditor 경로(/webcontent/ckeditor/...)는 본문 내 인라인 콘텐츠 — 첨부 아님.
        _PREVIEW_URL = re.compile(
            r"(synap|onlineviewer|docviewer|webviewer|preview|viewer\.do|/ckeditor/)",
            re.IGNORECASE,
        )

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
            if _PREVIEW_URL.search(file_url):
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
        # 수원·여주·세종·과천 등 eminwon 패턴: goDownLoad / goDownload (사이트마다 대소문자 다름)
        m = re.search(
            r"godownload\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
            combined,
            re.IGNORECASE,
        )
        if m:
            user_nm, sys_nm, path = m.group(1), m.group(2), m.group(3)
            base = self._eminwon_download_base(detail_url)
            return f"{base}?user_file_nm={quote(user_nm)}&sys_file_nm={quote(sys_nm)}&file_path={quote(path)}", user_nm
        # 안산시 fnFileDownLoad('FILE_ID') → /common/file/FileDown.do?file_id=FILE_ID (GET 동작 확인됨)
        m = re.search(r"fnFileDownLoad\s*\(\s*['\"]([^'\"]+)['\"]", combined, re.IGNORECASE)
        if m:
            from urllib.parse import urljoin
            file_id = m.group(1)
            base = urljoin(self.site.base_url + "/", "/common/file/FileDown.do")
            return f"{base}?file_id={quote(file_id)}", self._filename_from_url_or_text("", text)
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
        # 2) 표시 텍스트에서 파일명 추출
        # 안산시 a 태그 텍스트: "pdf 문서건설공사 안전점검(건축분야) ... 공고.pdf파일 다운로드 버튼"
        # → prefix("pdf 문서") + suffix("파일 다운로드 버튼") 제거 후 .확장자까지 자르기
        for source in (text, url):
            cleaned = source
            # prefix "pdf 문서|hwp 문서|hwpx 문서|..." 제거
            cleaned = re.sub(
                r"^\s*(pdf|hwp|hwpx|doc|docx|xls|xlsx|ppt|pptx|zip)\s*문서\s*",
                "", cleaned, flags=re.IGNORECASE,
            )
            # 공백 포함 파일명까지 허용해 첫 .확장자에서 자르기
            m = re.search(
                r"([^/'\"<>]{1,200}?\.(?:hwp|hwpx|pdf|docx?|xlsx?|pptx?|zip))",
                cleaned, re.IGNORECASE,
            )
            if m:
                name = m.group(1).strip()
                # 안산시처럼 앞에 "pdf|hwp..." 접두 그대로 붙은 경우 한 번 더 정리
                name = re.sub(
                    r"^(hwp|hwpx|pdf|doc|docx|xls|xlsx|ppt|pptx|zip)(?=[가-힣A-Z\(])",
                    "", name,
                )
                return name
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
