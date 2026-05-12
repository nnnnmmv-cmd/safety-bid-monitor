# 안전진단 입찰 자동 모니터링

지자체·공공기관이 자체 사이트에 직접 올리는 **"건설공사 안전점검"** 용역 공고를 매시간 자동으로 수집해 이메일로 알려주는 도구.

- 나라장터(G2B)는 외주 업체가 이미 처리하므로 제외
- 「건설기술진흥법」 제62조 안전점검 수행기관 지정/선정 공고가 주 대상
- 발주청 게시판 대부분이 eGovFrame 표준이라 한 어댑터로 다수 사이트 커버

---

# 비개발자용 단계별 가이드

> 이 안내는 macOS를 쓰는 분이 터미널을 거의 처음 다룬다는 가정으로 작성됐습니다.
> 명령어는 **회색 박스 안의 글자를 그대로 복사해서 터미널에 붙여넣고 엔터**를 누르면 됩니다.
> 막히면 **§99 자주 막히는 곳**을 보세요.

---

## 0단계 · 시스템이 어떻게 동작하는지 (1분)

```
   매시간 ────────────▶ 자동 실행
                            │
                            ▼
         (1) 지자체 게시판들을 순회하며 새 공고 찾기
                            │
                            ▼
         (2) "안전점검 수행기관" 키워드가 들어간 공고만 추리기
                            │
                            ▼
         (3) 이전에 본 적 없는 공고만 골라
                            │
                            ▼
         (4) Slack 채널(또는 이메일)로 한 번에 묶어서 발송
```

- 사용자가 한 번 셋업해두면 그 후엔 **Slack 채널만 보면 됨** (또는 이메일)
- 사이트를 더 추가하고 싶으면 `config/sites.yaml`이라는 메모장 파일에 한 줄만 추가
- 키워드를 늘리거나 줄이고 싶으면 `config/keywords.yaml`만 편집

> 알림 채널은 `.env` 파일에서 선택합니다:
> - `SLACK_WEBHOOK_URL`이 채워져 있으면 **Slack 우선**
> - 비어 있고 SMTP 설정이 있으면 **이메일**
> - 둘 다 없으면 로그에만 남기고 발송 안 함

---

## 1단계 · 터미널 여는 법 (30초)

1. 키보드에서 `⌘(Command) + Space` 동시에 누름 → Spotlight 검색창이 뜸
2. `터미널` 또는 `Terminal` 입력 → 엔터
3. 검은(또는 흰) 창이 열림 — 이게 "터미널"임. 명령어를 여기에 입력함

**용어 한 줄 설명:**
- **터미널** = 키보드로 컴퓨터에 명령을 내리는 창
- **명령어** = 영어 단어 또는 문장. 입력 후 엔터를 눌러야 실행됨
- **프롬프트** = 명령 입력을 기다리는 표시(`$` 또는 `%`)가 있는 줄

---

## 2단계 · 프로젝트 폴더로 이동 (10초)

터미널 창에 아래 명령을 그대로 복사해 붙여넣고 엔터:

```bash
cd "/Users/dev06/Safety diagnosis bid"
```

`cd`는 "change directory"의 약자 — 폴더 이동.
이후 단계의 모든 명령은 **이 폴더에서** 실행해야 함.

**확인 방법** — 아래 명령을 치면 폴더 안 파일들이 보임:

```bash
ls
```

`README.md`, `requirements.txt`, `src` 같은 이름이 나오면 성공.

---

## 3단계 · Python 설치 확인 (30초)

```bash
python3 --version
```

- `Python 3.11.x` 또는 `Python 3.12.x` 같은 글자가 나오면 OK → §4단계로
- `command not found`가 나오면 → Python을 설치해야 함

**Python 설치 (필요 시):**
1. 브라우저로 https://www.python.org/downloads/ 접속
2. 큰 노란색 "Download Python 3.x.x" 버튼 클릭
3. 다운로드된 `.pkg` 파일 더블클릭
4. "계속" → "동의" → "설치" 순서로 클릭 (관리자 비밀번호 입력)
5. 설치 끝나면 터미널을 닫고 다시 열어서 `python3 --version` 재확인

---

## 4단계 · 알림 채널 셋업 (Slack 추천)

### 4-A. Slack Webhook URL 발급 (5분)

> Slack 워크스페이스가 없다면 https://slack.com/get-started 에서 무료로 만들 수 있습니다. 회사 워크스페이스를 쓰는 경우 어드민 권한이 있어야 앱 설치가 가능합니다 (없으면 어드민에게 "Incoming Webhook 앱 설치 권한"을 요청하면 됨).

1. 브라우저로 https://api.slack.com/apps 접속 → 본인 Slack 계정으로 로그인
2. 오른쪽 위 초록색 **"Create New App"** 버튼 클릭
3. **"From scratch"** 선택
4. 다음 화면에서:
   - **App Name** 입력란에 `안전진단모니터` 입력
   - **Pick a workspace** 에서 알림을 받을 워크스페이스 선택
   - **"Create App"** 클릭
5. 앱 페이지가 열리면 왼쪽 메뉴 **"Incoming Webhooks"** 클릭
6. 오른쪽 위 토글 **"Activate Incoming Webhooks"** 를 **On** 으로
7. **⚠ 봇 사용자 + 권한 먼저 만들기 (이걸 안 하면 다음 단계에서 "봇 사용자가 없습니다" 에러가 남):**

   **(7-1) 봇 사용자 등록 — App Home 메뉴**
   - 왼쪽 메뉴 **"App Home"** 클릭 (Features 섹션 안)
   - **"Your App's Presence in Slack"** 섹션 → **"App Display Name"** 옆 **"Edit"** 클릭
   - **Display Name (Bot Name)**: `안전진단모니터봇` 같은 표시 이름
   - **Default username**: `safetybid` 같은 **소문자 영문/숫자/하이픈만** (한글 불가)
   - **"Add"** 클릭

   **(7-2) 봇 권한 추가 — OAuth & Permissions 메뉴**
   - 왼쪽 메뉴 **"OAuth & Permissions"** 클릭
   - **"Scopes"** 섹션의 **"Bot Token Scopes"** 표 아래 **"Add an OAuth Scope"** 클릭
   - `chat:write` 입력 후 추가 (그 위에 `incoming-webhook`이 자동 등록돼 있으면 그대로 둠, 없으면 같은 방식으로 추가)

   **(7-3) 워크스페이스에 설치**
   - 같은 페이지 위쪽 **"Install to Workspace"** (또는 "Reinstall to Workspace") 클릭
   - 알림 받을 채널 선택 → **"허용"** 클릭
8. 다시 왼쪽 메뉴 **"Incoming Webhooks"** 로 돌아가서 **"Add New Webhook to Workspace"** 버튼 클릭
9. **알림을 받을 채널**을 선택 (예: `#입찰모니터` 채널을 미리 만들어두면 좋음) → **"허용"** 클릭
10. 페이지로 돌아오면 "Webhook URLs for Your Workspace" 표 안에
    `https://hooks.slack.com/services/T.../B.../...` 형태의 URL이 보임 → **"Copy"** 버튼 클릭
11. 이 URL을 어딘가에 잠시 메모해두기 (다음 §6단계에서 `.env`에 붙여넣음)

> ⚠ Webhook URL은 비밀번호와 같습니다. 외부에 공유하지 마세요. 노출되면 위 페이지에서 "Revoke" 후 새로 만들면 됩니다.

### 4-B. (선택) Gmail 앱 비밀번호 발급

Slack 대신 이메일로 받고 싶을 때만 진행. 둘 다 설정해두면 Slack이 우선됨.

1. 브라우저로 https://myaccount.google.com/security 접속 → 본인 Gmail로 로그인
2. "Google에 로그인하는 방법" 섹션에서 **"2단계 인증"** 켜기 (이미 켜져 있으면 통과)
3. 그다음 https://myaccount.google.com/apppasswords 접속
4. "앱 이름" 입력란에 `안전진단모니터` 입력 → **"만들기"** 클릭
5. 16자리 비밀번호가 표시됨 (예: `abcd efgh ijkl mnop`) — **공백을 빼고** 16자만 메모

---

## 5단계 · 의존성 설치 (3분, 한 번만)

터미널에 아래 3줄을 한 줄씩 차례로 실행:

```bash
python3 -m venv .venv
```
(가상환경 만들기 — 이 프로젝트 전용 Python 공간)

```bash
source .venv/bin/activate
```
(가상환경 켜기 — 성공하면 프롬프트 앞에 `(.venv)`가 붙음)

```bash
pip install -r requirements.txt
```
(필요한 라이브러리 5개 설치 — 1~2분 걸림)

마지막 줄 출력 끝에 `Successfully installed ...`가 보이면 성공.

> **앞으로 터미널을 새로 열 때마다** §2단계의 `cd ...` + `source .venv/bin/activate` 두 줄을 다시 실행해야 합니다.

---

## 6단계 · `.env` 파일 만들기 (3분)

`.env`는 이 시스템에 비밀번호를 알려주는 작은 메모 파일임.

**6-A. 템플릿 복사:**

```bash
cp .env.example .env
```

**6-B. `.env` 파일 편집:**

```bash
open -e .env
```

이러면 macOS 기본 "텍스트 편집기"가 열림. **Slack을 쓸지 이메일을 쓸지**에 따라 채울 줄이 다름.

**Slack을 쓰는 경우 (추천):**

| 항목 | 무엇을 적나 |
|------|-----------|
| `SLACK_WEBHOOK_URL=` | `=` 뒤에 §4-A에서 복사한 URL을 그대로 붙여넣기. 예: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T01ABC.../B02XYZ.../...` |
| `SLACK_ADMIN_WEBHOOK_URL=` | 에러 알림을 다른 채널로 받고 싶을 때만. 비워두면 위 URL 재사용 |
| SMTP 관련 줄들 | 비워둬도 됨 |

**이메일을 쓰는 경우:**

| 항목 | 무엇을 적나 |
|------|-----------|
| `SMTP_USER=` | 본인 Gmail 주소 (예: `SMTP_USER=nnnnmmv@gmail.com`) |
| `SMTP_APP_PASSWORD=` | §4-B에서 받은 16자리, **공백 빼고**. 예: `SMTP_APP_PASSWORD=abcdefghijklmnop` |
| `NOTIFY_TO=` | 알림을 받을 이메일. 여러 명은 쉼표로: `a@x.com,b@y.com` |
| `NOTIFY_ADMIN=` | 에러 알림을 받을 본인 이메일 |
| `SLACK_WEBHOOK_URL=` | 비워두기 (값이 있으면 Slack이 우선됨) |

저장: `⌘ + S` → 창 닫기

> 둘 다 설정해두면 **Slack이 우선**으로 발송됩니다. Slack을 임시로 끄고 이메일로 받고 싶을 땐 `SLACK_WEBHOOK_URL=` 줄을 비우면 됨.

---

## 7단계 · DB(공고 저장소) 초기화 (10초)

```bash
python scripts/init_db.py
```

`OK — schema ready at .../data/bids.db` 라고 나오면 성공.
이건 한 번만 하면 됨.

---

## 8단계 · 알림 발송 테스트 (1분)

**Slack을 쓰는 경우:**

```bash
python scripts/send_test_slack.py
```

- `OK — sent to Slack webhook` 출력 + §4-A에서 선택한 채널에 테스트 메시지가 보이면 성공
- 메시지에 공고 2건이 카드 형태로 보여야 함
- 에러가 나면:
  - `Slack webhook HTTP 404`: Webhook URL이 잘못됨 → §4-A 다시
  - `Slack webhook HTTP 403`: 워크스페이스에서 앱이 비활성화됨 → 어드민 확인
  - `Slack webhook URL이 비어있습니다`: `.env`의 `SLACK_WEBHOOK_URL` 줄 확인

**이메일을 쓰는 경우:**

```bash
python scripts/send_test_email.py
```

- `OK — sent to ...@gmail.com` 출력 + Gmail 수신함에 테스트 메일이 보이면 성공
- 메일이 안 보이면 **스팸함**도 확인 (첫 발송은 분류될 수 있음)
- `SMTPAuthenticationError` 에러가 나면 §6-B의 앱 비밀번호를 다시 확인 (공백/오타)

---

## 9단계 · 모니터링할 사이트 등록 (사이트당 5~10분)

이 단계가 **가장 중요하고 손이 많이 가는 부분**입니다. 회사가 자주 들어가는 발주청 사이트 3~5곳을 등록해두면, 그 후로는 자동으로 동작합니다.

### 9-A. 사이트 정보 모으기

브라우저(Chrome 또는 Safari)에서:

1. 평소 들어가던 시청/도청/공공기관 홈페이지로 이동
2. 메뉴에서 **공지·공고**, **고시·공고**, **입찰공고**, **수의계약 공고** 같은 게시판을 찾기
3. 들어간 후 **검색창에 "안전점검" 입력 → 검색**
4. 검색 결과가 나오면 그 게시판이 모니터링 대상
5. **주소창 URL을 통째로 복사** (예: `https://www.example.go.kr/board/list.do?bbsId=BBSMSTR_000000000045`)

### 9-B. `sites.yaml` 편집

```bash
open -e config/sites.yaml
```

파일 안에는 이미 예시가 있음. 맨 아래에 다음 블록을 복사·수정해서 추가:

```yaml
  - name: 본인이 알아볼 이름        # 예: 서울특별시청, 부산광역시청
    adapter: egov
    base_url: https://www.example.go.kr   # 위에서 복사한 URL의 도메인 부분만
    list_url: https://www.example.go.kr/board/list.do  # 게시판 목록 URL (쿼리스트링 ? 앞까지)
    list_params:
      bbsId: BBSMSTR_000000000045         # ? 뒤의 "bbsId=" 값
    pagination:
      param: pageIndex
      max_pages: 3
    region: 서울                          # 알림 그룹화용 지역 태그
    enabled: true
```

**URL 쪼개는 법 (예시):**

원본 URL이 `https://www.example.go.kr/board/list.do?bbsId=BBSMSTR_000000000045&pageIndex=1`이면:
- `base_url`: `https://www.example.go.kr` (도메인까지)
- `list_url`: `https://www.example.go.kr/board/list.do` (? 앞까지)
- `list_params`의 `bbsId`: `BBSMSTR_000000000045` (`bbsId=` 다음 값)
- `pageIndex`는 시스템이 자동으로 붙여주므로 무시

저장: `⌘ + S` → 창 닫기

### 9-C. (만약 `bbsId`가 URL에 안 보이면)

일부 사이트는 게시판 ID가 URL이 아닌 페이지 내부 JavaScript에 숨겨져 있음. 그럴 땐:

1. 게시판 페이지에서 **우클릭 → "검사"** (또는 키보드 `F12`)
2. 개발자도구가 열리면 상단 탭 중 **"Network"** 클릭
3. 페이지를 새로고침(`⌘ + R`)
4. 왼쪽 목록에서 `list.do` 또는 `selectBoard...` 같은 이름의 항목 클릭
5. 오른쪽 패널의 **"Headers"** 탭에서 **Request URL** 줄을 확인 — 진짜 URL과 파라미터가 보임
6. 그 URL을 §9-B에 적용

너무 어려우면 일단 그 사이트는 건너뛰고 다른 사이트부터 시작하세요. (`enabled: false`로 두면 됨)

---

## 10단계 · 한 사이트만 동작 확인 (2분)

§9에서 등록한 사이트가 잘 읽히는지 확인:

```bash
python scripts/test_site.py --name "서울특별시청" --hours 720 --no-filter
```

- `--name` 뒤에는 §9-B에 적은 `name`값을 그대로 (큰따옴표로 감쌈)
- `--hours 720`은 "최근 30일치를 본다"는 의미
- `--no-filter`는 키워드 필터를 끄고 게시판이 파싱되는지부터 확인

**기대 출력:**
```
[서울특별시청] since=...
   raw count=42
  - [2026-05-10] 어떤 공고 제목
      url: https://...
  - [2026-05-09] 다른 공고 제목
   ...
```

- `raw count`가 0보다 크면 파싱 성공
- 0이면 사이트 구조가 표준에서 벗어남 — §99의 "raw count=0" 항목 참고

이번엔 필터를 켜고 다시:

```bash
python scripts/test_site.py --name "서울특별시청" --hours 720
```

`--no-filter`를 뺐으므로 "안전점검 수행기관" 같은 키워드가 포함된 공고만 보임. 0건이라도 정상 — 그저 그 사이트엔 최근에 안 올라온 것일 수 있음.

---

## 11단계 · 전체 한 번 수동 실행 (1분)

```bash
python -m src.monitor
```

- 활성화된 사이트들을 모두 돌면서 신규 공고를 수집
- 신규가 있으면 이메일 한 통이 본인에게 옴
- 신규 0건이면 이메일은 안 옴 (정상)

**같은 명령을 즉시 다시 실행**해도 두 번째엔 "신규 0건"이어야 함 → 중복 방지 정상 동작 확인.

---

## 12단계 · 매시간 자동 실행 등록 (2분)

매시간 5분에 자동 실행되도록 등록:

```bash
cp launchd/com.safetybid.monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.safetybid.monitor.plist
```

**등록 확인:**

```bash
launchctl list | grep safetybid
```

`com.safetybid.monitor`가 한 줄 보이면 성공.

**해제하고 싶을 때:**

```bash
launchctl unload ~/Library/LaunchAgents/com.safetybid.monitor.plist
```

> ⚠ **노트북이 꺼져 있거나 절전 모드면 그 시간엔 안 돌아갑니다.** 항상 모니터링하려면 노트북을 항시 켜두거나, 2단계에서 별도 서버로 옮기는 것을 고려.

---

## 13단계 · 평소 운영

**매일 받아볼 것:**
- 신규 공고가 있는 시간엔 Slack 채널(또는 Gmail)로 메시지가 옴
- Slack 메시지 첫 줄 예: `🛠 안전진단 신규 공고 3건`
- 메시지 안에 발주청별로 묶여서 공고명(클릭하면 상세 페이지)·마감일·추정가가 정리됨

**키워드를 더 정밀하게 하고 싶을 때:**

```bash
open -e config/keywords.yaml
```

- `include`(포함)에 키워드를 추가 → 더 많이 잡힘
- `exclude`(제외)에 키워드를 추가 → 무관 공고가 덜 옴
- 저장하면 다음 실행부터 자동 적용

**새 사이트를 추가하고 싶을 때:**
- §9-B와 똑같이 `sites.yaml`에 블록 하나 추가
- 저장하면 다음 실행부터 자동 포함

**DB 직접 보고 싶을 때:**

```bash
sqlite3 data/bids.db "SELECT site_name, COUNT(*) FROM bids GROUP BY site_name;"
```

각 사이트별로 몇 건이 쌓였는지 보임.

---

## 14단계 · 웹 대시보드 (터미널 없이 사용하기)

사이트 추가·키워드 수정·알림 설정·공고 확인·수동 실행을 모두 **브라우저 화면**에서 할 수 있습니다.

### 14-A. 실행 권한 한 번 부여 (최초 1회만)

터미널에 아래 명령을 그대로 붙여넣고 엔터:

```bash
chmod +x "/Users/dev06/Safety diagnosis bid/start_dashboard.command"
```

(아무 출력 없이 프롬프트가 다시 나오면 성공)

### 14-B. 더블클릭으로 대시보드 열기 (터미널 없이)

1. Finder에서 `/Users/dev06/Safety diagnosis bid/` 폴더 열기
2. **`안전진단 대시보드.app`** 파일 더블클릭
3. 터미널 창 없이 브라우저만 자동으로 `http://localhost:8502` 페이지가 열림

> **첫 실행 시 macOS 보안 경고:**
> "확인되지 않은 개발자가 만들었다" 또는 "악성 소프트웨어 검사를 할 수 없다"는 경고가 나오면:
> 1. 경고창의 **취소** 클릭
> 2. Finder에서 `안전진단 대시보드.app` **우클릭** → **열기** 선택
> 3. 다시 뜨는 작은 경고창에서 **열기** 클릭 → 그 후로는 더블클릭으로 바로 동작
> 또는: 시스템 설정 → 개인정보 보호 및 보안 → 맨 아래 차단된 항목 옆 **"그대로 열기"** 클릭.

**대안 (터미널이 뜨는 옛 방식)**: `start_dashboard.command` 파일을 더블클릭하면 터미널 창이 뜨면서 같은 대시보드가 열림. 로그를 실시간으로 보고 싶을 때 유용.

**끄는 법**:
- `.app` 으로 실행한 경우: 백그라운드에서 도므로, 터미널에서 `lsof -nP -iTCP:8502 -sTCP:LISTEN | awk 'NR>1 {print $2}' | xargs kill` 또는 그냥 노트북 재시작
- `.command` 로 실행한 경우: 터미널 창을 닫거나 `Ctrl + C`

### 14-C. 대시보드 메뉴 7가지

| 메뉴 | 무엇을 할 수 있나 |
|------|----------------|
| 📋 **공고 목록** | DB에 쌓인 신규 공고들을 표로. 사이트별 필터, "미발송만 보기" 옵션, 링크 클릭으로 원본 이동 |
| 📒 **발주청 명부** | 회사가 관리하는 발주청 표 (스프레드시트 형태). 구분/홈체크/한시진/한주검/투찰진행은 **드롭다운**, 날짜 항목은 **달력**, 그 외는 텍스트. 행 추가·삭제 자유 |
| 🌐 **사이트 관리** | 발주청 사이트의 **크롤링 설정**(URL·게시판 ID·셀렉터)을 폼으로 |
| 🔍 **키워드 관리** | 포함/제외 키워드를 한 줄에 하나씩. 저장 즉시 다음 실행에 반영 |
| 🔔 **알림 설정** | Slack Webhook, 이메일 SMTP, 동작 옵션을 폼에서 편집. "테스트 알림 보내기" 버튼 |
| ▶️ **수동 실행** | "🚀 지금 실행" 버튼 한 번 누르면 모니터링 1회 실행. 결과 출력이 화면에 실시간 표시 |
| 📜 **로그** | `logs/monitor.log`의 마지막 300줄 표시 (에러 추적용) |

**📒 발주청 명부 컬럼:** 업데이트 · 지자체명 · 구분(건축/토목/건축·토목) · 홈체크 · 한시진 · 한주검 · 투찰진행 · 신규제출일 · 시작일 · 종료일 · 운영기간(자동계산) · 공고예정일 · 이전 공고일 · 이전 마감일 · (1억원 미만)낙찰자 선정 방식 · 특이사항 · 지역 · 모니터링(체크박스)

### 14-D. 자주 하는 작업 흐름

**새 발주청을 명부에 추가 (영업 관리용):**
1. 사이드바에서 **📒 발주청 명부** 클릭
2. 표 맨 아래 **빈 행**에 지자체명 입력 → 구분/홈체크/투찰진행 등 드롭다운 선택, 날짜는 달력으로 선택
3. 자동 크롤링은 안 원하면 **모니터링** 체크박스를 빈 채로 두면 됨
4. 맨 아래 **💾 명부 저장**

**자동 크롤링까지 추가:**
1. 📒 발주청 명부에서 위처럼 행을 추가하고 **모니터링** 체크
2. 사이드바 **🌐 사이트 관리** 클릭 → 해당 발주청 항목 펼치기
3. `base_url` / `list_url` / `list_params`(bbsId 등) 입력 → **💾 모두 저장**
4. **▶️ 수동 실행** → **🚀 지금 실행** → 결과 확인

**키워드 추가/제거:**
1. **🔍 키워드 관리** 메뉴
2. 포함/제외 키워드 텍스트 박스에서 한 줄에 하나씩 편집
3. **💾 저장** → 다음 실행부터 적용

**알림 설정 변경:**
1. **🔔 알림 설정** 메뉴
2. 값 수정 → **💾 저장**
3. **📤 테스트 알림 보내기** 로 도달 확인

---

## 15단계 · 클라우드 배포 (Streamlit Community Cloud)

여러 사용자가 인터넷에서 접속하려면 클라우드 호스팅이 필요합니다. **무료**로 가능하고, 노트북을 끄셔도 동작합니다.

### 15-A. 큰 그림

```
┌────────────────────────┐      ┌──────────────────────────┐
│  Streamlit Cloud (UI)  │ ←──→ │  Turso DB (공고·명부 저장) │
│  github.com/<본인>/... │      └──────────────────────────┘
└────────────▲───────────┘                  ▲
             │                              │
             │ git push                     │ INSERT/SELECT
             │                              │
       ┌─────┴──────────────────────────────┴────┐
       │      GitHub Actions (매시간 cron)        │
       │      src.monitor를 실행 → DB에 적재      │
       └──────────────────────────────────────────┘
```

- **Streamlit Cloud**: 대시보드 UI 호스팅 (무료, 24/7)
- **Turso**: SQLite 호환 클라우드 DB (무료 8GB)
- **GitHub Actions**: 매시간 모니터링 실행 (무료 2000분/월)
- **GitHub Secrets**: Slack/SMTP 비밀번호 안전 보관

### 15-B. 사전 준비

1. **GitHub 계정** — https://github.com/signup (이메일·비밀번호만 있으면 5분)
2. **Streamlit Community Cloud 계정** — https://share.streamlit.io 에서 GitHub로 로그인
3. **Turso 계정** — https://turso.tech 에서 GitHub로 로그인 (5분)

위 3개 모두 무료, 카드 등록 없음.

### 15-C. 단계별 절차

이 단계는 양이 많아 한 번에 진행하기 어렵습니다. **저(Claude)와 같이 진행하시면 각 단계를 옆에서 가이드해드립니다.** 큰 순서만 미리 보면:

1. **GitHub 저장소 만들기** (5분)
   - github.com 우상단 `+` → `New repository`
   - 이름: `safety-bid-monitor` (또는 원하는 이름)
   - **Private** 선택 (소스에 회사 정보가 있으므로)
   - "Create repository" 클릭

2. **로컬 코드를 GitHub에 push** (10분 — 제가 git 명령 도와드림)
   - `.env`, `data/`, `logs/`, `config/users.yaml`은 자동 제외됨
   - Slack URL이나 비밀번호가 코드에 남아 있지 않도록 `git status`로 한 번 검토

3. **데이터 영속성 — Turso DB 만들기** (10분)
   - https://app.turso.tech → New Database → 이름 `safetybid` → Region `nrt`(도쿄)
   - 발급되는 **DB URL**과 **Auth Token** 메모
   - 기존 `data/bids.db`를 Turso로 이전 (제공해드릴 `scripts/migrate_to_turso.py`로 자동)

4. **Streamlit Cloud에 배포** (10분)
   - https://share.streamlit.io → "New app" → GitHub 저장소 선택
   - Main file path: `dashboard.py`
   - **Secrets** 탭에 환경변수 입력:
     ```toml
     SLACK_WEBHOOK_URL = "..."
     SLACK_ADMIN_WEBHOOK_URL = "..."
     TURSO_DATABASE_URL = "libsql://..."
     TURSO_AUTH_TOKEN = "..."
     COOKIE_SECRET = "임의의 긴 랜덤 문자열"
     ```
   - "Deploy" 클릭 → 1~2분 뒤 `https://<본인앱이름>.streamlit.app` 에서 접속 가능

5. **GitHub Actions로 매시간 모니터링 실행** (10분)
   - 저장소에 `.github/workflows/monitor.yml` 추가 (제가 작성해드림)
   - GitHub Repository Secrets에 동일 환경변수 등록
   - 매시간 0분에 자동 실행 → Turso DB에 적재 → 대시보드에 자동 반영

6. **사용자 추가** (3분)
   - 대시보드 로그인 후 👥 **사용자 관리** 메뉴에서 건축/토목 담당자 계정 생성
   - 카테고리 권한 설정 → 각 사용자는 자기 카테고리만 보임

### 15-D. 진행 방식 권장

위 6단계를 한 번에 다 하기보다, **단계 1부터 차례로** 진행하면서 막힐 때마다 저에게 물어보시면 됩니다. 다음 단계로 가시려면 **"15단계 1번부터 시작"** 같은 식으로 말씀 주시면 그때그때 명령어와 클릭 위치를 안내해드립니다.

### 15-E. 트레이드오프

| 항목 | 로컬(.app) | Streamlit Cloud |
|------|-----------|----------------|
| 비용 | 무료 | 무료 |
| 노트북 꺼져도 동작 | ❌ | ✅ |
| 외부 출장에서 접속 | ❌ (LAN만) | ✅ |
| 멀티유저 | △ (LAN 같은 와이파이) | ✅ |
| 셋업 시간 | 끝남 | 60분 정도 |
| 데이터 위치 | 노트북 | Turso (클라우드) |

지금은 **로컬에서 충분히 익숙해진 뒤** 클라우드로 가시는 것을 권장합니다 — 로컬에서 인증·권한 동작이 확인되면 클라우드 이전은 같은 코드를 그대로 옮기는 작업입니다.

---

## §99 · 자주 막히는 곳

| 증상 | 원인 / 조치 |
|------|-----------|
| 터미널에 `command not found: python3` | §3단계로 가서 Python 설치 |
| 터미널에 `cd: no such file or directory` | 폴더 경로 오타 — `"/Users/dev06/Safety diagnosis bid"`처럼 큰따옴표로 감싸기 |
| 명령 실행 시 `ModuleNotFoundError: No module named 'requests'` | 가상환경이 꺼져 있음 → `source .venv/bin/activate` 다시 실행 |
| `test_site.py`에서 `raw count=0` | 그 사이트가 표준 게시판이 아님 → 일단 `enabled: false`로 두고 다른 사이트부터 운영 |
| `SMTPAuthenticationError` | §4-B의 앱 비밀번호 다시 발급 후 `.env` 갱신 (공백·따옴표 없이) |
| `Slack webhook HTTP 404 / 410` | Webhook URL이 폐기됨 → §4-A 따라 새로 발급 |
| `Slack webhook HTTP 403` | 워크스페이스에서 앱이 차단됨 → 어드민에게 활성화 요청 |
| Slack 메시지 / 이메일이 도착 안 함 | `logs/monitor.log` 열어서 마지막 줄에 발송 실패 에러가 있는지 확인 |
| 대시보드 더블클릭 후 다른 프로젝트 화면(`homecheck-sales-hub` 등)이 뜸 | 다른 Streamlit이 같은 포트를 쓰고 있음. 우리 대시보드는 **8502** 사용 — 주소창에 `http://localhost:8502` 직접 입력 |
| 대시보드 포트도 누가 쓰고 있어 안 뜸 | `lsof -nP -iTCP:8502 -sTCP:LISTEN`으로 확인 후 `start_dashboard.command`의 `--server.port` 번호를 8503 등 다른 값으로 변경 |
| 자동 실행이 안 되는 것 같음 | `tail -50 logs/launchd.err.log` 명령으로 최근 에러 50줄 확인 |
| 똑같은 공고가 반복해서 옴 | 그 사이트가 공고번호를 안 노출함 → 어댑터를 사이트 전용으로 추가해야 함. 일단 무시하거나 그 사이트를 `enabled: false` |
| `.env` 파일이 안 보임 | `.`(점)으로 시작하는 파일은 Finder에서 기본 숨김. 터미널에서 `open -e .env`로 열기 |

**로그 보는 법:**

```bash
tail -100 logs/monitor.log
```

(최근 100줄을 보여줌. 에러는 `[ERROR]`로 표시됨)

---

# 개발자용 빠른 참고

## 디렉토리 구조

```
.
├── config/
│   ├── sites.yaml          # 사이트 목록 (사용자 편집)
│   └── keywords.yaml       # 필터 규칙 (사용자 편집)
├── data/
│   ├── bids.db             # SQLite (자동 생성)
│   └── snapshots/
├── logs/
├── src/
│   ├── adapters/
│   │   ├── base.py         # Adapter 추상 클래스 + BidPosting 데이터클래스
│   │   ├── egov.py         # eGovFrame 표준 게시판 어댑터 (핵심)
│   │   ├── eminwon.py      # eminwon 시스템 (현재는 egov 상속)
│   │   └── registry.py     # 어댑터 이름 → 클래스 매핑
│   ├── config.py           # .env·YAML 로딩
│   ├── db.py               # SQLite 스키마·CRUD
│   ├── filter.py           # 키워드 매칭
│   ├── notifier.py         # Gmail SMTP 발송 + HTML 렌더
│   ├── monitor.py          # 메인 파이프라인 진입점
│   └── utils.py            # 날짜·금액·D-day 파싱
├── scripts/
│   ├── init_db.py
│   ├── test_site.py        # 단일 사이트 어댑터 검증
│   └── send_test_email.py
└── launchd/
    └── com.safetybid.monitor.plist
```

## 어댑터 추가하기

1. `src/adapters/custom_<name>.py` 생성, `Adapter` 상속
2. `fetch(self, since: datetime) -> list[BidPosting]` 구현
3. `src/adapters/registry.py`의 `_REGISTRY`에 등록
4. `config/sites.yaml`에서 `adapter: custom_<name>` 사용

## 2단계 확장 후보

- 첨부파일(과업지시서) 자동 다운로드 → `data/attachments/{notice_id}/`
- Claude API로 과업지시서 자동 요약 + 회사 자격 매칭
- Slack/카카오톡 알림 추가
- Streamlit 대시보드
- 동적 사이트용 Playwright 어댑터
- 24/7 운영 (오라클 무료 VM 등)
