"""JavaScript 동적 로드 사이트용 어댑터. Chromium headless로 렌더링 후 HTML 추출.

용인시·고양시·경기도청처럼 게시판 데이터를 JS로 동적 로드하는 사이트 대응.
"""
from __future__ import annotations

import logging
import time

from .egov import EgovAdapter

logger: logging.Logger = logging.getLogger(__name__)


class PlaywrightAdapter(EgovAdapter):
    """EgovAdapter 상속 + _get만 Playwright(Chromium)로 override.

    HTML 파싱·detail URL 추출·첨부 추출 등은 부모 클래스 로직 그대로 사용.
    """

    def _get(self, url: str, params: dict[str, str] | None = None, retries: int = 3) -> str:
        full_url = url
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v)
            if qs:
                sep = "&" if "?" in url else "?"
                full_url = f"{url}{sep}{qs}"

        # detail URL(eminwon 등 다른 호스트의 정적 페이지)은 Chromium 안 띄우고 일반 HTTP.
        # list_url과 호스트가 다르면 detail로 판단.
        from urllib.parse import urlparse
        try:
            list_host = urlparse(self.site.list_url).netloc
            req_host = urlparse(full_url).netloc
            if list_host and req_host and list_host != req_host:
                return super()._get(url, params, retries)
        except Exception:
            pass

        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                return self._render(full_url)
            except Exception as exc:
                last_err = exc
                logger.debug("[playwright] retry %d for %s: %s", attempt + 1, full_url[:80], exc)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Playwright GET {full_url} failed after {retries} retries: {last_err}")

    def _render(self, url: str) -> str:
        """Chromium 한 번 띄워서 페이지 로드 → 렌더링된 HTML 반환."""
        # sync_playwright는 매 호출마다 새 인스턴스 — 한 사이트당 list page + N detail page 호출 시 N+1번
        # cron 1회당 사이트 ~3개(동적)뿐이라 부담 적음. 더 빠르게 만들려면 추후 인스턴스 재사용.
        from playwright.sync_api import sync_playwright

        timeout_ms = int(self.runtime.http_timeout_sec * 1000 * 3)  # 동적 페이지는 더 여유
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                    ),
                    locale="ko-KR",
                    viewport={"width": 1280, "height": 900},
                    ignore_https_errors=True,  # 일부 한국 정부 사이트의 오래된 SSL
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                logger.info("[pw-debug] goto 후 길이=%d, frames=%d", len(page.content()), len(page.frames))
                try:
                    page.wait_for_selector("table, ul.board_list, div.list, div.contents", timeout=5000)
                except Exception:
                    pass

                # 검색어 자동 입력
                search_input_sel = self.site.selectors.get("search_input")
                search_keyword = self.site.selectors.get("search_keyword")
                if search_input_sel and search_keyword:
                    target = self._find_search_target(page, search_input_sel)
                    logger.info("[pw-debug] 검색 input 발견: %s", target is not None)
                    if target:
                        try:
                            target.fill(search_keyword)
                            target.press("Enter")
                            page.wait_for_timeout(3000)
                            logger.info("[pw-debug] 검색 후 frames=%d", len(page.frames))
                        except Exception as exc:
                            logger.warning("[pw-debug] 검색 입력 실패: %s", exc)

                # 용인시(eminwon) — iframe form1 + searchDetail 분석해서 각 행에 data-action 박기
                # → egov.py의 data-action 폴백 로직이 자동으로 detail URL을 채택
                self._annotate_eminwon_detail_urls(page)

                # 메인 + iframe 콘텐츠 합치기
                html_parts: list[str] = []
                try:
                    main_content = page.content()
                    html_parts.append(main_content)
                    logger.info("[pw-debug] main page.content() 길이=%d", len(main_content))
                except Exception as exc:
                    logger.warning("[pw-debug] main page.content() 실패: %s", exc)
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    try:
                        fc = frame.content()
                        html_parts.append(f"\n<!-- iframe: {frame.name or 'unnamed'} -->\n{fc}")
                        logger.info("[pw-debug] frame %s 길이=%d", frame.name or "?", len(fc))
                    except Exception as exc:
                        logger.warning("[pw-debug] frame 읽기 실패 (%s): %s", frame.name, exc)
                        continue
                html = "\n".join(html_parts)
                logger.info("[pw-debug] 최종 길이=%d", len(html))
                time.sleep(self.runtime.request_delay_sec)
                return html
            finally:
                browser.close()

    def _annotate_eminwon_detail_urls(self, page) -> None:  # type: ignore[no-untyped-def]
        """eminwon iframe(form1+searchDetail) → 행마다 data-action 절대 detail URL 박기.

        용인시 같은 eminwon 사이트는 detail 진입이 POST form 기반인데,
        실측 결과 GET URL로 호출해도 detail HTML이 정상 응답함.
        form1의 hidden field 전체와 searchDetail 함수의 method/methodnm을 합쳐
        절대 URL을 만들고, 각 row의 link 요소에 data-action으로 박는다.
        egov.py의 data-action 폴백이 이를 자동으로 채택.
        """
        js = """() => {
            // searchDetail 함수 source를 HTML에서 직접 파싱 (IIFE/스코프 때문에 typeof로 못 잡는 사이트 대응)
            const html = document.documentElement.outerHTML;
            const sdMatch = html.match(/function\\s+searchDetail\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\}/);
            if (!sdMatch) return [0];
            const src = sdMatch[0];
            const m1 = src.match(/method\\.value\\s*=\\s*['"]([^'"]+)['"]/);
            const m2 = src.match(/methodnm\\.value\\s*=\\s*['"]([^'"]+)['"]/);
            if (!m1 || !m2) return [0];
            const method = m1[1], methodnm = m2[1];
            // action: 함수 안의 f.action = "..." 우선, 없으면 form.action
            const actMatch = src.match(/\\.action\\s*=\\s*['"]([^'"]+)['"]/);
            let action = actMatch ? actMatch[1] : '';
            if (action && !/^https?:/.test(action)) {
                try { action = new URL(action, document.baseURI).href; } catch(e) {}
            }
            const form = document.forms['form1'];
            if (!action && form) action = form.action;
            if (!action) return [0];
            // hidden field
            const params = new URLSearchParams();
            if (form) {
                form.querySelectorAll('input[type=hidden]').forEach(i => {
                    if (['method','methodnm','not_ancmt_mgt_no','pageIndex'].includes(i.name)) return;
                    if (i.value) params.set(i.name, i.value);
                });
            }
            params.set('method', method);
            params.set('methodnm', methodnm);
            const baseQs = params.toString();
            let count = 0;
            document.querySelectorAll('tr').forEach(tr => {
                let oc = '';
                tr.querySelectorAll('a, td, button').forEach(el => {
                    const v = el.getAttribute('onclick') || '';
                    if (v.includes('searchDetail')) oc = v;
                });
                const m = oc.match(/searchDetail\\(['"](\\d+)['"]/);
                if (m) {
                    const url = action + (baseQs ? ('?' + baseQs + '&') : '?') + 'not_ancmt_mgt_no=' + m[1];
                    tr.querySelectorAll('a, td').forEach(el => el.setAttribute('data-action', url));
                    count++;
                }
            });
            return [count];
        }"""
        # main_frame도 포함 — 성남시는 form1·searchDetail이 main page에 있고 iframe은 list만 표시.
        # form1이 없는 frame은 evaluate가 [0] 반환하므로 안전.
        for frame in page.frames:
            try:
                r = frame.evaluate(js)
                if r and r[0] > 0:
                    logger.info("[pw] eminwon detail URL %d개 annotated (frame=%s)", r[0], frame.name or "main")
            except Exception as exc:
                logger.debug("[pw] annotate 실패 (%s): %s", frame.name, exc)

    def _find_search_target(self, page, selector):  # type: ignore[no-untyped-def]
        """검색 input 찾기. iframe 우선 — 게시판이 iframe 안에 있을 때 메인 페이지의 사이트 전체 검색이 잡히지 않게.

        사이트의 selectors.search_frame_hint가 있으면 그 URL 패턴을 가진 frame을 우선.
        """
        frame_hint = (self.site.selectors.get("search_frame_hint") or "").lower()
        # 1) iframe 우선 (frame_hint 있으면 그것 우선, 없으면 모든 iframe)
        candidate_frames = [f for f in page.frames if f != page.main_frame]
        if frame_hint:
            candidate_frames.sort(key=lambda f: 0 if frame_hint in f.url.lower() else 1)
        for frame in candidate_frames:
            try:
                el = frame.query_selector(selector)
                if el:
                    return el
            except Exception:
                continue
        # 2) iframe에서 못 찾으면 메인 페이지 fallback
        try:
            return page.query_selector(selector)
        except Exception:
            return None
