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

## 현재 운영 상태 (2026-06-04 알림 재개)
- 알림 정상 발송 중. monitor + healthcheck cron 모두 동작. 통합 게시판 7곳(구리·성남·안산·오산·평택·하남·화성) 합쳐서 1 row + category="건축·토목"로 정리. 의정부·양평·광주도 "건축·토목"으로 변경. notifier에 `_classify_post_category()` 추가 — "건축·토목" 사이트의 글은 title 분야 키워드(건축공사·근린생활시설 / 도로·하수관 등) 감지해서 동적 채널 분기.
- 발주청 명부 UI: category SelectBox → 건축/토목 체크박스 2개 분리. 저장 시 두 체크박스 → category 합성("건축·토목" / "건축" / "토목").
- 글 분야 키워드는 `src/notifier.py`의 `_ARCH_KEYWORDS`/`_CIVIL_KEYWORDS`. 필요 시 추가/조정.

## Gotchas
- `slack_sdk.files_upload_v2`는 ok=true 응답해도 워크스페이스 정책으로 `channels=[]` (채널 attach 실패) 가능 → 메시지 본문에 원본 URL을 `attachments_raw`로 박는 우회책 사용 중
- `import_excel.py`는 동일 사이트명을 카테고리 suffix로 자동 분리 (예: "성남시" → "성남시-건축"/"성남시-토목")
- `notified=True` 박힌 글은 cron 재처리 안 함. 강제 재발송 시 DB row 삭제 or `send_one_test.py`
- eminwon은 POST form이지만 **GET URL로도 detail 응답** (`OfrAction.do?method=selectOfrNotAncmt&not_ancmt_mgt_no=N&jndinm=OfrNotAncmtEJB&context=NTIS`)
- openclaw proxy(`localhost:3456`)는 `claude-sonnet-4-5`/`4-6` 어느 쪽 요청도 응답 model이 `claude-sonnet-4`로 라우팅됨 (모델 선택권 우리에게 없음)
- 사이트 가격 상한은 `src/monitor.py`의 `SITE_PRICE_CAP` dict로 관리 (안양·과천 1억 미만만)

## 코드 스타일
- `from __future__ import annotations` 사용
- 한국어 주석 (단순 `#` 인라인, 인자 설명은 docstring)
- 응답 model 필드 빈값 등 가짜 정상 응답 의심되면 디버그 로깅으로 응답 디테일 까보기
