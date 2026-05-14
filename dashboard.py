"""안전진단 입찰 모니터 — 비개발자용 웹 대시보드.

실행: `./.venv/bin/streamlit run dashboard.py` 또는 `start_dashboard.command` 더블클릭.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import dotenv_values, load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

# .env를 import 시점에 한 번만 — store가 환경변수 필요
load_dotenv(Path(__file__).resolve().parent / ".env")

from src import store
from src.auth import ALL_CATEGORIES, User, authenticate, has_password, list_users
from src.config import LOG_DIR, PROJECT_ROOT, load_config
from src.notifier import notify_new_postings

ENV_PATH: Path = PROJECT_ROOT / ".env"
MONITOR_LOG: Path = LOG_DIR / "monitor.log"

st.set_page_config(page_title="안전진단 입찰 모니터", layout="wide", page_icon="🛠")

# ---------- 로그인 ----------

def _render_login() -> None:
    from src.auth import upsert_user

    st.title("🛠 안전진단 입찰 모니터")

    # admin 비밀번호 미설정 상태 → 초기 셋업 폼
    if not _admin_has_password():
        st.warning("처음 사용입니다. **admin 비밀번호**를 설정해주세요.")
        with st.form("init_admin_form"):
            pw = st.text_input("새 admin 비밀번호 (6자 이상)", type="password")
            pw2 = st.text_input("비밀번호 다시 입력", type="password")
            submitted = st.form_submit_button("🔐 비밀번호 설정", type="primary", use_container_width=True)
        if submitted:
            if len(pw) < 6:
                st.error("비밀번호는 6자 이상이어야 합니다.")
                return
            if pw != pw2:
                st.error("두 비밀번호가 일치하지 않습니다.")
                return
            upsert_user("admin", password=pw, role="admin", categories=ALL_CATEGORIES)
            st.success("admin 비밀번호가 설정되었습니다. 로그인 해주세요.")
            st.rerun()
        return

    st.caption("로그인 후 이용하실 수 있습니다.")
    with st.form("login_form"):
        username = st.text_input("아이디")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
    if submitted:
        if not username or not password:
            st.warning("아이디와 비밀번호를 모두 입력하세요.")
            return
        u = authenticate(username, password)
        if u is None:
            st.error("아이디 또는 비밀번호가 잘못되었습니다.")
            return
        st.session_state["user"] = u
        st.rerun()


def _admin_has_password() -> bool:
    return has_password("admin")


if "user" not in st.session_state:
    _render_login()
    st.stop()

user: User = st.session_state["user"]


# ---------- 데이터 헬퍼 (Supabase) ----------

def read_sites() -> list[dict[str, Any]]:
    return store.list_sites()


def write_sites(sites: list[dict[str, Any]]) -> None:
    store.replace_all_sites(sites)


def read_keywords() -> dict[str, list[str]]:
    kw = store.list_keywords()
    return {
        "include": kw.get("include", []),
        "exclude": kw.get("exclude", []),
        "require_match_in": kw.get("match_in", ["title", "body"]) or ["title", "body"],
    }


def write_keywords(data: dict[str, list[str]]) -> None:
    store.replace_keywords(
        include=data.get("include", []),
        exclude=data.get("exclude", []),
        match_in=data.get("require_match_in", ["title", "body"]),
    )


def read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(str(ENV_PATH)).items()}


def write_env(values: dict[str, str]) -> None:
    """기존 주석/순서를 보존하면서 KEY=VALUE만 갱신. 없는 키는 끝에 추가."""
    existing_lines: list[str] = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

    handled: set[str] = set()
    output: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            output.append(f"{key}={values[key]}")
            handled.add(key)
        else:
            output.append(line)
    for key, val in values.items():
        if key not in handled:
            output.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(output) + "\n", encoding="utf-8")


def _matched_keywords_list(value: Any) -> list[str]:
    """Supabase jsonb는 list로 오지만 옛 SQLite 데이터는 JSON 문자열일 수 있음."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return [str(v) for v in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


# ---------- 사이드바 ----------

st.sidebar.title("🛠 안전진단 모니터")
st.sidebar.markdown(f"**{user.name}** ({user.role})")
st.sidebar.caption(f"카테고리: {', '.join(user.categories) if user.categories else '(없음)'}")
if st.sidebar.button("🚪 로그아웃", use_container_width=True):
    del st.session_state["user"]
    st.rerun()
st.sidebar.divider()

ADMIN_PAGES: list[str] = [
    "📋 공고 목록",
    "📒 발주청 명부",
    "🔍 키워드 관리",
    "🔔 알림 설정",
    "▶️ 수동 실행",
    "📜 로그",
    "👥 사용자 관리",
]
VIEWER_PAGES: list[str] = [
    "📋 공고 목록",
    "📒 발주청 명부",
]
PAGES: list[str] = ADMIN_PAGES if user.role == "admin" else VIEWER_PAGES
page = st.sidebar.radio("메뉴", PAGES, label_visibility="collapsed")
st.sidebar.divider()

env_now = read_env()
slack_set = bool((env_now.get("SLACK_WEBHOOK_URL") or "").strip())
smtp_set = bool((env_now.get("SMTP_USER") or "").strip() and (env_now.get("SMTP_APP_PASSWORD") or "").strip())
channel_label = "Slack" if slack_set else ("이메일" if smtp_set else "❌ 미설정")
st.sidebar.metric("현재 알림 채널", channel_label)

sites_now = read_sites()


def _visible_to_user(site: dict[str, Any]) -> bool:
    if user.role == "admin":
        return True
    cat = str(site.get("category") or "").strip()
    if not cat:
        return False
    return cat in (user.categories or [])


visible_sites = [s for s in sites_now if _visible_to_user(s)]
enabled_visible = sum(1 for s in visible_sites if s.get("enabled"))
st.sidebar.metric(
    "보이는 사이트 (활성/전체)",
    f"{enabled_visible} / {len(visible_sites)}",
    help="본인 카테고리 권한으로 필터링된 수치",
)


# ---------- 페이지: 공고 목록 ----------

def page_bids() -> None:
    st.header("📋 수집된 공고")

    visible_site_names = {s.get("name") for s in visible_sites}
    rows = store.fetch_recent_bids(limit=500)
    if user.role != "admin":
        rows = [r for r in rows if r.get("site_name") in visible_site_names]
    if not rows:
        st.info("아직 보실 수 있는 공고가 없습니다. **▶️ 수동 실행** 메뉴에서 한 번 돌려보세요." if user.role == "admin" else "본인 카테고리에 해당하는 공고가 아직 없습니다.")
        return

    sites = sorted({r["site_name"] for r in rows if r.get("site_name")})
    col_a, col_b, col_c = st.columns([2, 1, 1])
    with col_a:
        site_filter = st.multiselect("사이트", sites, default=[])
    with col_b:
        only_unnotified = st.checkbox("미발송만 보기", value=False)
    with col_c:
        only_keyword = st.checkbox("키워드 매칭 있음만", value=False)

    filtered: list[dict[str, Any]] = []
    for d in rows:
        if site_filter and d.get("site_name") not in site_filter:
            continue
        if only_unnotified and d.get("notified"):
            continue
        if only_keyword and not _matched_keywords_list(d.get("matched_keywords")):
            continue
        filtered.append(d)

    st.caption(f"총 {len(filtered)}건 표시 (DB 전체 {len(rows)}건)")

    table_data: list[dict[str, Any]] = []
    for d in filtered:
        kw_text = ", ".join(_matched_keywords_list(d.get("matched_keywords")))
        table_data.append({
            "사이트": d.get("site_name"),
            "공고명": d.get("title"),
            "게시일": (d.get("posted_at") or "")[:10],
            "마감일": (d.get("deadline_at") or "")[:10],
            "추정가": d.get("estimated_price"),
            "키워드": kw_text,
            "알림": "✅" if d.get("notified") else "⏳",
            "링크": d.get("url"),
        })
    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "링크": st.column_config.LinkColumn("링크", display_text="열기"),
            "추정가": st.column_config.NumberColumn(format="%d원"),
        },
    )

    with st.expander("⚠ 위험 작업"):
        col1, col2 = st.columns(2)
        with col1:
            if st.button("선택 사이트의 알림 상태 초기화 (재발송 대상으로)"):
                if site_filter:
                    n = store.reset_notified_for_sites(site_filter)
                    st.success(f"{n}건 초기화됨")
                else:
                    st.warning("사이트 필터를 먼저 선택하세요.")
        with col2:
            if st.button("⚠️ 전체 공고 삭제 (테스트용)"):
                n = store.delete_all_bids()
                st.success(f"{n}건 삭제됨")


# ---------- 페이지: 발주청 명부 (스프레드시트형) ----------

ROSTER_COLUMNS: list[tuple[str, str]] = [
    ("last_updated", "업데이트"),
    ("name", "지자체명"),
    ("category", "구분"),
    ("homecheck", "홈체크"),
    ("hansijin", "한시진"),
    ("hanjugum", "한주검"),
    ("bidding_status", "투찰진행"),
    ("new_submission_date", "신규제출일"),
    ("period_start", "시작일"),
    ("period_end", "종료일"),
    ("operating_period", "운영기간"),
    ("announce_planned_date", "공고예정일"),
    ("previous_announce_date", "이전 공고일"),
    ("previous_deadline", "이전 마감일"),
    ("under_100m_winner_method", "(1억원 미만) 낙찰자 선정 방식"),
    ("above_100m_winner_method", "(1억원 이상) 낙찰자 선정 방식"),
    ("bid_submission_method", "입찰서 제출 방식"),
    ("performance_proof", "실적증명"),
    ("work_overlap_doc", "업무중첩도 확인서류"),
    ("note", "특이사항"),
    ("region", "지역"),
    ("crawl_status", "🔧"),
    ("enabled", "모니터링"),
]


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _from_date(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value or "").strip()


def _calc_period(start: str, end: str) -> str:
    s = _to_date(start)
    e = _to_date(end)
    if not s or not e:
        return ""
    months = (e.year - s.year) * 12 + (e.month - s.month)
    if months >= 12 and months % 12 == 0:
        return f"{months // 12}년"
    if months >= 1:
        return f"{months}개월"
    return f"{(e - s).days}일"


def page_roster() -> None:
    st.header("📒 발주청 명부")
    if user.role == "admin":
        st.caption("회사가 관리하는 발주청 목록입니다. 컬럼은 드롭다운/날짜/텍스트로 입력할 수 있고 행 추가·삭제도 가능합니다. 변경 후 **저장** 버튼을 눌러야 반영됩니다.")
    else:
        st.caption(f"본인 카테고리({', '.join(user.categories)})에 해당하는 발주청만 표시됩니다. 편집 권한은 관리자에게 문의하세요.")

    sites = read_sites()
    if user.role != "admin":
        sites = [s for s in sites if _visible_to_user(s)]
    table_rows: list[dict[str, Any]] = []
    for s in sites:
        table_rows.append({
            "last_updated": _to_date(s.get("last_updated")),
            "name": s.get("name", ""),
            "category": s.get("category", "") or "",
            "homecheck": s.get("homecheck", "") or "",
            "hansijin": s.get("hansijin", "") or "",
            "hanjugum": s.get("hanjugum", "") or "",
            "bidding_status": s.get("bidding_status", "") or "",
            "new_submission_date": _to_date(s.get("new_submission_date")),
            "period_start": _to_date(s.get("period_start")),
            "period_end": _to_date(s.get("period_end")),
            "operating_period": _calc_period(s.get("period_start", ""), s.get("period_end", "")),
            "announce_planned_date": _to_date(s.get("announce_planned_date")),
            "previous_announce_date": _to_date(s.get("previous_announce_date")),
            "previous_deadline": _to_date(s.get("previous_deadline")),
            "under_100m_winner_method": s.get("under_100m_winner_method", "") or "",
            "above_100m_winner_method": s.get("above_100m_winner_method", "") or "",
            "bid_submission_method": s.get("bid_submission_method", "") or "",
            "performance_proof": s.get("performance_proof", "") or "",
            "work_overlap_doc": s.get("work_overlap_doc", "") or "",
            "note": s.get("note", "") or "",
            "region": s.get("region", "") or "",
            "crawl_status": "✓" if (s.get("base_url") and s.get("list_url")) else "—",
            "enabled": bool(s.get("enabled", False)),
        })

    df = pd.DataFrame(table_rows, columns=[c for c, _ in ROSTER_COLUMNS])

    column_config = {
        "last_updated": st.column_config.DateColumn("업데이트", format="YYYY-MM-DD"),
        "name": st.column_config.TextColumn("지자체명", required=True, width="medium"),
        "category": st.column_config.SelectboxColumn(
            "구분", options=["", "건축", "토목", "건축·토목"], required=False
        ),
        "homecheck": st.column_config.SelectboxColumn("홈체크", options=["", "O", "X"]),
        "hansijin": st.column_config.SelectboxColumn("한시진", options=["", "O", "X"]),
        "hanjugum": st.column_config.SelectboxColumn("한주검", options=["", "O", "X"]),
        "bidding_status": st.column_config.SelectboxColumn(
            "투찰진행", options=["", "진행", "불가", "보류"]
        ),
        "new_submission_date": st.column_config.DateColumn("신규제출일", format="YYYY-MM-DD"),
        "period_start": st.column_config.DateColumn("시작일", format="YYYY-MM-DD"),
        "period_end": st.column_config.DateColumn("종료일", format="YYYY-MM-DD"),
        "operating_period": st.column_config.TextColumn("운영기간", disabled=True, help="시작일/종료일로 자동 계산"),
        "announce_planned_date": st.column_config.DateColumn("공고예정일", format="YYYY-MM-DD"),
        "previous_announce_date": st.column_config.DateColumn("이전 공고일", format="YYYY-MM-DD"),
        "previous_deadline": st.column_config.DateColumn("이전 마감일", format="YYYY-MM-DD"),
        "under_100m_winner_method": st.column_config.TextColumn("(1억원 미만) 낙찰자 선정 방식", width="medium"),
        "above_100m_winner_method": st.column_config.TextColumn("(1억원 이상) 낙찰자 선정 방식", width="medium"),
        "bid_submission_method": st.column_config.TextColumn("입찰서 제출 방식", width="medium"),
        "performance_proof": st.column_config.TextColumn("실적증명", width="medium"),
        "work_overlap_doc": st.column_config.TextColumn("업무중첩도 확인서류", width="medium"),
        "note": st.column_config.TextColumn("특이사항", width="large"),
        "region": st.column_config.TextColumn("지역", width="small"),
        "crawl_status": st.column_config.TextColumn(
            "🔧", disabled=True, width="small",
            help="크롤링 URL 설정 여부 — ✓ 설정됨 / — 미설정. 아래 '크롤링 설정' 섹션에서 편집",
        ),
        "enabled": st.column_config.CheckboxColumn("모니터링", help="체크 시 매시간 자동 수집"),
    }

    edited: pd.DataFrame = st.data_editor(
        df,
        column_config=column_config,
        hide_index=True,
        num_rows="dynamic" if user.role == "admin" else "fixed",
        disabled=user.role != "admin",
        use_container_width=True,
        key="roster_editor",
    )

    c1, c2, c3 = st.columns([1, 1, 3])
    if user.role == "admin" and c1.button("💾 명부 저장", type="primary"):
        existing_by_name = {s.get("name"): s for s in sites if s.get("name")}
        new_sites: list[dict[str, Any]] = []
        for _, row in edited.iterrows():
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            base = dict(existing_by_name.get(name, {}))
            base["name"] = name
            base["enabled"] = bool(row.get("enabled"))
            base["region"] = str(row.get("region") or "")
            base["category"] = str(row.get("category") or "")
            base["last_updated"] = _from_date(row.get("last_updated"))
            base["homecheck"] = str(row.get("homecheck") or "")
            base["hansijin"] = str(row.get("hansijin") or "")
            base["hanjugum"] = str(row.get("hanjugum") or "")
            base["bidding_status"] = str(row.get("bidding_status") or "")
            base["new_submission_date"] = _from_date(row.get("new_submission_date"))
            base["period_start"] = _from_date(row.get("period_start"))
            base["period_end"] = _from_date(row.get("period_end"))
            base["announce_planned_date"] = _from_date(row.get("announce_planned_date"))
            base["previous_announce_date"] = _from_date(row.get("previous_announce_date"))
            base["previous_deadline"] = _from_date(row.get("previous_deadline"))
            base["under_100m_winner_method"] = str(row.get("under_100m_winner_method") or "")
            base["above_100m_winner_method"] = str(row.get("above_100m_winner_method") or "")
            base["bid_submission_method"] = str(row.get("bid_submission_method") or "")
            base["performance_proof"] = str(row.get("performance_proof") or "")
            base["work_overlap_doc"] = str(row.get("work_overlap_doc") or "")
            base["note"] = str(row.get("note") or "")
            # 신규 행의 기본 크롤링 골격
            base.setdefault("adapter", "egov")
            base.setdefault("base_url", "")
            base.setdefault("list_url", "")
            base.setdefault("list_params", {})
            base.setdefault("pagination", {})
            new_sites.append(base)
        write_sites(new_sites)
        st.success(f"저장 완료 — {len(new_sites)}건 (모니터링 활성 {sum(1 for s in new_sites if s.get('enabled'))}건)")

    if c2.button("🔄 다시 읽기"):
        st.rerun()

    with st.expander("ℹ 사용 팁"):
        st.markdown(
            "- 새 행을 추가하려면 표 맨 아래의 **빈 행**에 직접 입력하세요. 행 삭제는 행 왼쪽 체크박스 선택 후 **휴지통** 아이콘.\n"
            "- 자동 크롤링까지 원하면 저장 후 **아래 🔧 크롤링 설정** 섹션에서 해당 발주청을 선택해 URL과 게시판 ID를 입력하세요.\n"
            "- `운영기간`은 시작일/종료일을 채우면 자동으로 계산됩니다.\n"
            "- `🔧` 컬럼이 `✓`면 크롤링 URL이 설정된 상태, `—`면 미설정.\n"
            "- `모니터링`이 체크되고 🔧이 `✓`인 사이트만 매시간 자동 수집됩니다."
        )

    # ===== 크롤링 설정 폼 (선택한 발주청 1개) =====
    st.divider()
    st.subheader("🔧 크롤링 설정")
    st.caption("표에서 발주청을 추가·저장한 뒤, 여기서 그 발주청의 URL/게시판 ID를 설정하면 자동 수집 대상이 됩니다.")

    site_names = [s["name"] for s in sites if s.get("name")]
    if not site_names:
        st.info("발주청을 먼저 표에 추가하고 **💾 명부 저장**을 누르세요.")
        return

    if user.role != "admin":
        st.info("크롤링 설정 편집은 관리자만 가능합니다.")
        return

    selected_name = st.selectbox("발주청 선택", site_names, key="crawl_target")
    target = next((s for s in sites if s.get("name") == selected_name), None)
    if target is None:
        return

    with st.form(f"crawl_form_{selected_name}"):
        c1, c2 = st.columns(2)
        adapter_opts = ["egov", "eminwon"]
        cur_adapter = target.get("adapter", "egov")
        adapter = c1.selectbox(
            "어댑터",
            adapter_opts,
            index=adapter_opts.index(cur_adapter) if cur_adapter in adapter_opts else 0,
            help="egov: 행정안전부 표준 게시판 (대다수). eminwon: 일부 시·도 자체 시스템.",
        )
        bbs_id = c2.text_input(
            "bbsId (게시판 ID)",
            value=str((target.get("list_params") or {}).get("bbsId", "")),
            help="URL 쿼리스트링의 'bbsId=' 다음 값. 예: BBSMSTR_000000000045",
        )

        base_url = st.text_input(
            "base_url (도메인까지)",
            value=str(target.get("base_url") or ""),
            placeholder="https://www.example.go.kr",
        )
        list_url = st.text_input(
            "list_url (게시판 목록 URL, ? 앞까지)",
            value=str(target.get("list_url") or ""),
            placeholder="https://www.example.go.kr/board/list.do",
        )

        c1, c2 = st.columns(2)
        page_param = c1.text_input(
            "페이징 쿼리 키",
            value=str((target.get("pagination") or {}).get("param", "pageIndex")),
        )
        max_pages = c2.number_input(
            "최대 페이지 수",
            min_value=1, max_value=20,
            value=int((target.get("pagination") or {}).get("max_pages", 3)),
        )

        with st.expander("고급 셀렉터 (표준 어댑터가 파싱 못 할 때만)"):
            sel = target.get("selectors") or {}
            sel_row = st.text_input("row 셀렉터", value=str(sel.get("row", "")), placeholder="table.board_list tbody tr")
            sel_title = st.text_input("title 셀렉터", value=str(sel.get("title", "")), placeholder="td.subject a")
            sel_date = st.text_input("date 셀렉터", value=str(sel.get("date", "")), placeholder="td.date")

        with st.expander("추가 list_params (bbsId 외에 더 필요한 GET 쿼리)"):
            extra_params = {k: v for k, v in (target.get("list_params") or {}).items() if k != "bbsId"}
            extra_text = st.text_area(
                "한 줄에 키=값",
                value="\n".join(f"{k}={v}" for k, v in extra_params.items()),
                height=80,
                placeholder="menuId=200",
            )

        submitted = st.form_submit_button("💾 크롤링 설정 저장", type="primary")

    if submitted:
        list_params: dict[str, str] = {}
        for line in extra_text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            k, v = line.split("=", 1)
            list_params[k.strip()] = v.strip()
        if bbs_id.strip():
            list_params["bbsId"] = bbs_id.strip()

        pagination = {
            "param": (page_param or "pageIndex").strip(),
            "max_pages": int(max_pages),
        }

        selectors = {k: v for k, v in {
            "row": sel_row.strip(),
            "title": sel_title.strip(),
            "date": sel_date.strip(),
        }.items() if v}

        updated = dict(target)
        updated["adapter"] = adapter
        updated["base_url"] = base_url.strip().rstrip("/")
        updated["list_url"] = list_url.strip()
        updated["list_params"] = list_params
        updated["pagination"] = pagination
        if selectors:
            updated["selectors"] = selectors
        elif "selectors" in updated:
            updated.pop("selectors")

        store.upsert_sites([updated])
        st.success(f"'{selected_name}' 크롤링 설정 저장 완료")
        st.rerun()


# ---------- 페이지: 키워드 ----------

def page_keywords() -> None:
    st.header("🔍 키워드 관리")
    st.caption("한 줄에 하나씩 입력합니다. 변경 후 **저장**을 눌러야 적용됩니다.")

    kw = read_keywords()
    include_text = st.text_area(
        "포함 키워드 (제목/본문에 하나라도 있으면 후보)",
        value="\n".join(kw["include"]),
        height=220,
    )
    exclude_text = st.text_area(
        "제외 키워드 (하나라도 있으면 탈락)",
        value="\n".join(kw["exclude"]),
        height=180,
    )
    match_in = st.multiselect(
        "어디서 매칭할지",
        ["title", "body"],
        default=kw["require_match_in"],
    )

    if st.button("💾 저장", type="primary"):
        write_keywords({
            "include": [line.strip() for line in include_text.splitlines() if line.strip()],
            "exclude": [line.strip() for line in exclude_text.splitlines() if line.strip()],
            "require_match_in": match_in or ["title"],
        })
        st.success("저장 완료")


# ---------- 페이지: 알림 설정 ----------

def page_notify() -> None:
    st.header("🔔 알림 설정")
    env = read_env()

    st.subheader("Slack")
    slack = st.text_input(
        "SLACK_WEBHOOK_URL",
        value=env.get("SLACK_WEBHOOK_URL", ""),
        type="password",
        help="https://api.slack.com/apps 에서 발급한 Incoming Webhook URL",
    )
    slack_admin = st.text_input(
        "SLACK_ADMIN_WEBHOOK_URL (에러 알림 별도 채널, 옵션)",
        value=env.get("SLACK_ADMIN_WEBHOOK_URL", ""),
        type="password",
    )

    st.subheader("이메일 (Slack을 안 쓸 때만)")
    c1, c2 = st.columns(2)
    smtp_user = c1.text_input("SMTP_USER (Gmail 주소)", value=env.get("SMTP_USER", ""))
    smtp_pw = c2.text_input("SMTP_APP_PASSWORD (16자리, 공백 없이)", value=env.get("SMTP_APP_PASSWORD", ""), type="password")
    notify_to = st.text_input("NOTIFY_TO (수신 이메일, 여러 명은 쉼표)", value=env.get("NOTIFY_TO", ""))
    notify_admin = st.text_input("NOTIFY_ADMIN (에러 수신 이메일)", value=env.get("NOTIFY_ADMIN", ""))

    st.subheader("동작 옵션")
    c1, c2, c3 = st.columns(3)
    lookback = c1.number_input("LOOKBACK_HOURS (몇 시간 전부터)", min_value=1, max_value=720, value=int(env.get("LOOKBACK_HOURS") or 48))
    delay = c2.number_input("REQUEST_DELAY_SEC (요청 간 대기, 초)", min_value=0.0, max_value=10.0, value=float(env.get("REQUEST_DELAY_SEC") or 1.0), step=0.5)
    timeout = c3.number_input("HTTP_TIMEOUT_SEC", min_value=5, max_value=120, value=int(float(env.get("HTTP_TIMEOUT_SEC") or 15)))

    c1, c2 = st.columns([1, 1])
    if c1.button("💾 저장", type="primary"):
        write_env({
            "SLACK_WEBHOOK_URL": slack,
            "SLACK_ADMIN_WEBHOOK_URL": slack_admin,
            "SMTP_USER": smtp_user,
            "SMTP_APP_PASSWORD": smtp_pw,
            "NOTIFY_TO": notify_to,
            "NOTIFY_ADMIN": notify_admin,
            "LOOKBACK_HOURS": str(lookback),
            "REQUEST_DELAY_SEC": str(delay),
            "HTTP_TIMEOUT_SEC": str(timeout),
        })
        st.success("저장 완료 — 다음 실행부터 적용됩니다.")

    if c2.button("📤 테스트 알림 보내기"):
        try:
            # 방금 저장한 env를 다시 로드해서 사용
            for k, v in {
                "SLACK_WEBHOOK_URL": slack, "SLACK_ADMIN_WEBHOOK_URL": slack_admin,
                "SMTP_USER": smtp_user, "SMTP_APP_PASSWORD": smtp_pw,
                "NOTIFY_TO": notify_to, "NOTIFY_ADMIN": notify_admin,
            }.items():
                os.environ[k] = v or ""
            cfg = load_config()
            sample = [{
                "site_name": "테스트 발주청",
                "title": "건설공사 안전점검 수행기관 지정 공고 (대시보드 테스트)",
                "deadline_at": datetime.now().isoformat(timespec="seconds"),
                "estimated_price": 12_500_000,
                "url": "https://example.go.kr/board/view?id=1",
            }]
            notify_new_postings(cfg, sample)
            st.success("발송 완료 — Slack 채널/이메일 수신함을 확인하세요.")
        except Exception as exc:
            st.error(f"발송 실패: {exc}")


# ---------- 페이지: 수동 실행 ----------

def page_run() -> None:
    st.header("▶️ 모니터링 수동 실행")
    st.caption("지금 즉시 한 번 돌립니다. 결과는 아래에 표시되고 신규 공고가 있으면 Slack/이메일로도 발송됩니다.")

    if st.button("🚀 지금 실행", type="primary"):
        with st.status("실행 중...", expanded=True) as status:
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "src.monitor"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                st.text(proc.stdout[-3000:] if proc.stdout else "(stdout 없음)")
                if proc.stderr:
                    st.text(proc.stderr[-3000:])
                if proc.returncode == 0:
                    status.update(label="✅ 실행 완료", state="complete")
                else:
                    status.update(label=f"⚠ 종료 코드 {proc.returncode}", state="error")
            except subprocess.TimeoutExpired:
                status.update(label="⏱ 10분 초과 — 강제 중단", state="error")
            except Exception as exc:
                status.update(label="❌ 예외", state="error")
                st.code(traceback.format_exc())


# ---------- 페이지: 로그 ----------

def page_logs() -> None:
    st.header("📜 실행 로그")
    if not MONITOR_LOG.exists():
        st.info("아직 로그 파일이 없습니다.")
        return
    text = MONITOR_LOG.read_text(encoding="utf-8", errors="replace")
    tail = "\n".join(text.splitlines()[-300:])
    st.caption(f"파일: `{MONITOR_LOG}` (마지막 300줄 표시)")
    st.code(tail or "(빈 파일)", language="log")


# ---------- 페이지: 사용자 관리 (admin) ----------

def page_users() -> None:
    from src.auth import delete_user, upsert_user

    st.header("👥 사용자 관리")
    st.caption("대시보드에 로그인할 수 있는 사용자를 관리합니다. 비밀번호는 bcrypt로 해시 저장됩니다.")

    users = list_users()
    if users:
        st.subheader("기존 사용자")
        table = [{
            "아이디": u.username,
            "이름": u.name,
            "이메일": u.email,
            "권한": u.role,
            "카테고리": ", ".join(u.categories) or "(없음)",
        } for u in users]
        st.dataframe(table, hide_index=True, use_container_width=True)

        with st.expander("기존 사용자 비밀번호 재설정 / 권한 변경"):
            target_name = st.selectbox("대상", [u.username for u in users], key="upd_target")
            new_pw = st.text_input("새 비밀번호 (변경 시만)", type="password", key="upd_pw")
            target_obj = next(u for u in users if u.username == target_name)
            new_role = st.selectbox(
                "권한", ["admin", "viewer"],
                index=["admin", "viewer"].index(target_obj.role if target_obj.role in ("admin", "viewer") else "viewer"),
                key="upd_role",
            )
            new_cats = st.multiselect("카테고리", ALL_CATEGORIES, default=target_obj.categories, key="upd_cats")
            new_name = st.text_input("이름", value=target_obj.name, key="upd_name")
            new_email = st.text_input("이메일", value=target_obj.email, key="upd_email")
            c1, c2 = st.columns(2)
            if c1.button("💾 변경 저장"):
                upsert_user(
                    target_name,
                    password=new_pw if new_pw else None,
                    name=new_name,
                    email=new_email,
                    role=new_role,
                    categories=new_cats,
                )
                st.success(f"'{target_name}' 갱신됨")
                st.rerun()
            if c2.button("🗑 사용자 삭제"):
                if target_name == user.username:
                    st.error("자기 자신은 삭제할 수 없습니다.")
                else:
                    delete_user(target_name)
                    st.success(f"'{target_name}' 삭제됨")
                    st.rerun()

    st.divider()
    st.subheader("➕ 사용자 추가")
    with st.form("add_user_form", clear_on_submit=True):
        new_username = st.text_input("아이디 (영문/숫자, 공백 없이)")
        new_password = st.text_input("초기 비밀번호 (6자 이상)", type="password")
        c1, c2 = st.columns(2)
        new_name2 = c1.text_input("이름")
        new_email2 = c2.text_input("이메일")
        c1, c2 = st.columns(2)
        new_role2 = c1.selectbox("권한", ["viewer", "admin"])
        new_cats2 = c2.multiselect("카테고리", ALL_CATEGORIES, default=["건축"])
        submitted = st.form_submit_button("➕ 추가", type="primary")
        if submitted:
            if not new_username or not new_password:
                st.warning("아이디와 비밀번호는 필수입니다.")
            elif len(new_password) < 6:
                st.warning("비밀번호는 6자 이상이어야 합니다.")
            elif any(u.username == new_username for u in users):
                st.error(f"이미 존재하는 아이디입니다: {new_username}")
            else:
                upsert_user(
                    new_username,
                    password=new_password,
                    name=new_name2 or new_username,
                    email=new_email2,
                    role=new_role2,
                    categories=new_cats2,
                )
                st.success(f"'{new_username}' 추가됨")
                st.rerun()


# ---------- 라우팅 ----------

if page == "📋 공고 목록":
    page_bids()
elif page == "📒 발주청 명부":
    page_roster()
elif page == "🔍 키워드 관리":
    page_keywords()
elif page == "🔔 알림 설정":
    page_notify()
elif page == "▶️ 수동 실행":
    page_run()
elif page == "📜 로그":
    page_logs()
elif page == "👥 사용자 관리":
    page_users()
