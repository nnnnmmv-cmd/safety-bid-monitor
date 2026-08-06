# 안전진단 입찰 모니터 — Claude 작업 가이드

## 실행 환경
- Python 가상환경: `.venv/bin/python` (system python 사용 금지)
- `.env` + Supabase 접속 활성화: 스크립트 첫 줄에 `from src.config import load_config; load_config()` 호출
- sites/keywords는 `sites.yaml`이 아닌 **Supabase DB**에서 로드 (`sites.yaml`엔 예시만)
- 로그: `logs/monitor.log`, macOS launchd cron: KST 9·12·15·18·21·0·3·6시 5분

## 검증 스크립트
- `.venv/bin/python scripts/test_site.py --name "사이트명" --no-filter --hours 720` — 어댑터 raw 추출 확인
- `.venv/bin/python scripts/send_one_test.py --name "사이트명"` — 한 글 강제 슬랙 발송 (DB 안 건드림)

## Adapter 패턴 (src/adapters/)
- 어댑터 추가 위치: `registry.py`의 `_REGISTRY` (egov / eminwon / playwright)
- 사이트별 list→detail URL 변환: `egov.py`의 `_DETAIL_URL_RULES` 리스트에 추가
- `_extract_title_and_url` 폴백: title selector → 가장 긴 `<a>` → 가장 긴 `<td>` (eminwon에서 a 없는 행 대응)
- `_LegacyHTTPSAdapter` (base.py): cipher SECLEVEL=1 + verify=False, 한국 정부 사이트 SSL 호환. attachments.py도 동일 mount
- `_maybe_fetch_detail` 가드: `detail_url.startswith(list_url)`면 skip (URL 변환 실패 후 무한 재요청 방지)
- `PlaywrightAdapter._get`: list_url과 호스트 다르면 일반 requests로 fallback (Chromium 매번 재기동 비용 차단)
- `_annotate_eminwon_detail_urls`: main_frame + iframe 모두 검사. `searchDetail` 함수는 `document.documentElement.outerHTML`에서 정규식으로 파싱 (IIFE/스코프 우회). 행 내 모든 `<a>`,`<td>`에 `data-action` 박음

## 현재 운영 상태 (2026-06-07 재가동 + 채널 통합)
- Supabase 재시작 완료, monitor + healthcheck cron 재가동.
- **슬랙 채널 통합** — 건축/토목 채널 분리 운영 폐지, 새 통합 채널 하나로 모든 공고 발송. `.env`의 `SLACK_CHANNEL_ALL`에 통합 채널 ID 설정 시 `_resolve_channel_ids`가 카테고리 무관 그 채널만 반환. 미설정 시 기존 건축/토목 분기 폴백 (하위호환). **사용자가 .env에 SLACK_CHANNEL_ALL 추가 + 봇 /invite 필요.**
- 헬스체크 슬랙 메시지 — 메인은 제목+합계 한 줄, 상세는 thread reply (`maybe_send_slack`).
- URL 회신 반영(2026-06): 조달청 통합명부만 쓰는 7곳(농어촌공사·양평군 환경사업소·한국가스공사·한국어촌어항공단·한국철도공사·한국토지주택공사·한국도로공사) 모니터링 OFF. 고양시 se=01 + playwright 전환. 부천시 playwright 전환(eminwon iframe). 국가철도공단 list_url을 ebid.kr.or.kr/krn/krnBidList.do로 교체. 모니터링 ON 38곳.
- 통합 게시판 7곳(구리·성남·안산·오산·평택·하남·화성)은 1 row + category="건축·토목". `_classify_post_category()`는 channel_all 설정 시 사실상 미사용 (채널 분기 자체가 없음).
- 발주청 명부 UI: category SelectBox → 건축/토목 체크박스 2개 분리. 저장 시 두 체크박스 → category 합성.

## Gotchas
- `slack_sdk.files_upload_v2`는 ok=true 응답해도 워크스페이스 정책으로 `channels=[]` (채널 attach 실패) 가능 → 메시지 본문에 원본 URL을 `attachments_raw`로 박는 우회책 사용 중
- `import_excel.py`는 동일 사이트명을 카테고리 suffix로 자동 분리 (예: "성남시" → "성남시-건축"/"성남시-토목")
- `notified=True` 박힌 글은 cron 재처리 안 함. 강제 재발송 시 DB row 삭제 or `send_one_test.py`
- notice_id는 detail URL의 고유 키(`notAncmtMgtNo`·`bbs_seq`·`sno` 등, `[?&]` 경계 매칭) 우선, 없으면 title 폴백 — **게시판 순번(row=N) 사용 금지** (순번은 새 글마다 밀려서 같은 글이 중복 인식됨. 2026-07 포천·구리·평택 48건 중복 사례). 키 추가 시 `_infer_notice_id`와 기존 DB 마이그레이션 동시 진행 필요
- 경로형 상세 URL(쿼리 파라미터가 아닌 `/board/{id}` 형태)은 사이트 `selectors.detail_url_template`에 `https://.../{seq}` 지정 (성남시 사례). `_DETAIL_URL_RULES`는 쿼리형만 처리
- 목록 1페이지가 10건인데 GET 페이징이 막힌 사이트(화성시)는 **list_url에 제목검색 파라미터를 박아** 대상 공고만 받게 할 것 — 안 그러면 게시량 많은 날 구조적 누락 (화성시 `q_sc=notAncmtSj&q_sv=안전점검`)
- eminwon은 POST form이지만 **GET URL로도 detail 응답** (`OfrAction.do?method=selectOfrNotAncmt&not_ancmt_mgt_no=N&jndinm=OfrNotAncmtEJB&context=NTIS`)
- openclaw proxy(`localhost:3456`)는 `claude-sonnet-4-5`/`4-6` 어느 쪽 요청도 응답 model이 `claude-sonnet-4`로 라우팅됨 (모델 선택권 우리에게 없음)
- 사이트 가격 상한은 `src/monitor.py`의 `SITE_PRICE_CAP` dict로 관리 (안양·과천 1억 미만만)
- 지자체 공고는 수집 직후 `src/hub_sync.py`로 허브 `arch_bid_notices`에 `source='local'` upsert (허브 입찰 관리 화면 합류용). **`notified_at` 필수** — 비우면 허브 아침 cron이 재알림. bids 저장·자체 슬랙 알림은 그대로 유지(이중 저장). 마감일이 실무상 항상 비어 있어 `bid_clse_dt`는 `posted_at+14일` 폴백. 백필: `scripts/backfill_hub_notices.py`, 검증: `scripts/test_hub_sync.py`
- 결과 공고(지정/선정 결과)는 `summarizer.is_result_notice()`로 판별 → `extract_result_fields()`로 선정업체·금액·대상공사를 뽑아 `store.merge_bid_extracted_fields()`로 **병합**(기존 7필드 덮지 않음). 게시판 전용 건은 이 공고문이 유일한 결과 원천. 백필: `scripts/backfill_results.py`, 검증: `scripts/test_result_extract.py`
- 건축 투찰 불가 발주청은 `src/notifier.py`의 `ARCH_NOTIFY_BLOCKED_SITES`(과천시·과천도시공사·의왕시·양주시·부천시) — **알림만 제외, 수집·DB 저장은 유지**(모집·등록명부 공고 추적 필요). 토목·분야미표시는 그대로 발송. 사이트 매칭은 **정확 일치 필수** — "양주시"⊂"남양주시", "과천시"⊂"과천도시공사"라 부분매칭 쓰면 엉뚱한 곳이 막힘. 검증: `scripts/test_arch_block.py`
- 사이트 분야 필터는 `src/monitor.py`의 `SITE_CATEGORY_FILTER` dict (현재 부천시=토목만). **사이트 category 값으로 전역 필터링 금지** — 통합 게시판인데 category가 한쪽으로 적힌 곳(용인시-토목·연천군 맑은물·여주시 등)이 많아 정상 글이 대량 누락됨 (검증 시 60일치 22건 중 18건이 오탐)

## 코드 스타일
- `from __future__ import annotations` 사용
- 한국어 주석 (단순 `#` 인라인, 인자 설명은 docstring)
- 응답 model 필드 빈값 등 가짜 정상 응답 의심되면 디버그 로깅으로 응답 디테일 까보기
