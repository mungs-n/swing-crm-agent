import os

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv
from datetime import timedelta

from utils.auth import is_logged_in, render_login_form, render_signup_form, render_logout_button, render_onboarding_screen
from utils.data_loader import DATASET_OPTIONS, get_dataset_source, fmt_amount, currency_config, load_users_orders, load_recent_active_users

PURPLE = "#7C3AED"

load_dotenv()

# 로컬 개발 중엔 로그인 화면이 매번 번거로우므로, .env에 SKIP_LOGIN=true를 넣으면
# 로그인 없이 데이터셋 선택만으로 바로 대시보드에 들어갈 수 있다.
# 배포 환경(Render 등)에는 이 값을 넣지 않으므로 실제 데모/운영에서는 로그인이 그대로 유지된다.
SKIP_LOGIN = os.getenv("SKIP_LOGIN", "false").lower() == "true"

LOGO_PATH = "assets/ATHLEPA 로고.png"
THEME_CSS_PATH = "assets/theme.css"

st.set_page_config(
    page_title="ATHLEPA CRM",
    page_icon=LOGO_PATH,
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo(LOGO_PATH, size="large")

with open(THEME_CSS_PATH, "r", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def _pct_delta(current, previous):
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


def _trend_pill(direction, label):
    cls = {"up": "trend-up", "down": "trend-down"}.get(direction, "trend-neutral")
    arrow = {"up": "▲", "down": "▼"}.get(direction, "")
    return f"<span class='trend-pill {cls}'>{arrow} {label}</span>"


def _stat_card(label, value, trend_html="", sub="", alert=False, sub_inline=False):
    label_color = "color:#E8590C" if alert else ""
    if sub and sub_inline:
        value_html = (
            f"<div class='stat-value'>{value} "
            f"<span class='stat-sub' style='margin-left:6px'>{sub}</span></div>"
        )
    else:
        value_html = f"<div class='stat-value'>{value}</div>" + (f"<div class='stat-sub'>{sub}</div>" if sub else "")
    with st.container(border=True):
        st.markdown(
            f"<div class='stat-card-top'><span class='stat-label' style='{label_color}'>{label}</span>{trend_html}</div>"
            + value_html,
            unsafe_allow_html=True,
        )


def render_home():
    company_name = st.session_state.get("auth_company_name", "ATHLEPA")
    col_head, col_badge = st.columns([5, 2])
    with col_head:
        st.markdown(f"<h1>안녕하세요, {company_name} 팀</h1>", unsafe_allow_html=True)
    with col_badge:
        st.markdown(
            "<div style='text-align:right;margin-top:10px'>"
            "<span style='font-size:11px;color:var(--athlepa-muted-text);"
            "padding:4px 10px;border-radius:8px;background:var(--athlepa-muted);"
            "border:1px solid var(--athlepa-border)'>마지막 업데이트: 방금 전</span></div>",
            unsafe_allow_html=True,
        )

    try:
        dataset_source = get_dataset_source()
        # 홈 화면은 13만 행짜리 events 전체가 필요 없어서(대시보드 탭1만 그게 필요함),
        # users/orders만 가볍게 불러온다 — 로그인 직후 첫 화면이 몇십 초씩 걸리던 걸 줄이기 위함.
        users, orders = load_users_orders(dataset_source)

        # events 없이도(예: 데이콘) 기준 날짜를 잡을 수 있도록, 주문일 최댓값을 기준일로 쓴다.
        latest_date = orders["order_date"].max() if not orders.empty else pd.Timestamp.now()

        # --- 최근 30일 vs 그 이전 30일 활성 고객 (필요한 60일치, user_id/timestamp만 조회) ---
        since = (latest_date - timedelta(days=60)).strftime("%Y-%m-%d")
        until = latest_date.strftime("%Y-%m-%d")
        recent_events = load_recent_active_users(dataset_source, since, until)

        if recent_events.empty:
            active_30d = active_prev_30d = 0
        else:
            active_30d = recent_events[recent_events["timestamp"] >= latest_date - timedelta(days=30)]["user_id"].nunique()
            active_prev_30d = recent_events[
                (recent_events["timestamp"] < latest_date - timedelta(days=30))
                & (recent_events["timestamp"] >= latest_date - timedelta(days=60))
            ]["user_id"].nunique()
        active_trend = _pct_delta(active_30d, active_prev_30d)

        gmv_total = orders["total_amount"].sum()
        gmv_30d = orders.loc[orders["order_date"] >= latest_date - timedelta(days=30), "total_amount"].sum()
        gmv_prev_30d = orders.loc[
            (orders["order_date"] < latest_date - timedelta(days=30))
            & (orders["order_date"] >= latest_date - timedelta(days=60)),
            "total_amount",
        ].sum()
        gmv_trend = _pct_delta(gmv_30d, gmv_prev_30d)

        last_order = orders.groupby("user_id")["order_date"].max()
        dormant_count = int((last_order < latest_date - timedelta(days=60)).sum())

        # 캠페인 발송 이력은 지금 ATHLEPA 계정 전용 기능(데이콘 등 다른 회사는 아직 캠페인을
        # 운영하지 않음)이라, 다른 회사로 로그인했을 때 ATHLEPA의 캠페인 데이터가 그대로
        # 보이면 안 된다. dataset_source가 athlepa일 때만 실제로 읽어온다.
        if get_dataset_source() == "athlepa":
            try:
                history_df = pd.read_csv("data/campaign_history.csv")
            except FileNotFoundError:
                history_df = pd.DataFrame()
        else:
            history_df = pd.DataFrame()

        this_month = pd.Timestamp.now().strftime("%Y-%m")
        campaigns_this_month = history_df[history_df.get("발송일시", pd.Series(dtype=str)).astype(str).str.startswith(this_month)] if not history_df.empty else pd.DataFrame()
        scheduled_this_month = campaigns_this_month["상태"].astype(str).str.contains("예약").sum() if not campaigns_this_month.empty else 0
        completed_this_month = len(campaigns_this_month) - scheduled_this_month

        if get_dataset_source() == "athlepa":
            try:
                scheduled_df = pd.read_csv("data/scheduled_emails.csv", encoding="utf-8-sig")
            except FileNotFoundError:
                scheduled_df = pd.DataFrame()
        else:
            scheduled_df = pd.DataFrame()
        next_scheduled = None
        if not scheduled_df.empty:
            scheduled_df = scheduled_df.copy()
            scheduled_df["_dt"] = pd.to_datetime(scheduled_df["발송예정일시"], errors="coerce")
            future = scheduled_df[scheduled_df["_dt"] >= pd.Timestamp.now()]
            if not future.empty:
                next_scheduled = future.sort_values("_dt").iloc[0]
                next_scheduled_count = int((scheduled_df["_dt"] == next_scheduled["_dt"]).sum())

        # --- KPI 카드 4개 ---
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        kcol1, kcol2, kcol3, kcol4 = st.columns(4)
        with kcol1:
            _stat_card(
                "활성 고객 (30일)", f"{active_30d:,}명",
                _trend_pill("up" if active_trend >= 0 else "down", f"{active_trend:+.1f}%"),
            )
        with kcol2:
            _stat_card(
                "누적 GMV", fmt_amount(gmv_total),
                _trend_pill("up" if gmv_trend >= 0 else "down", f"{gmv_trend:+.1f}%"),
            )
        with kcol3:
            _stat_card(
                "휴면 고객 (60일+)", f"{dormant_count:,}명",
                _trend_pill("down", "주의"), alert=True,
            )
        with kcol4:
            _stat_card(
                "이번 달 캠페인", f"{len(campaigns_this_month)}건",
                sub=f"예약 {scheduled_this_month} · 완료 {completed_this_month}",
                sub_inline=True,
            )

        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

        # --- 메인 2열: 왼쪽(GMV차트+최근캠페인) / 오른쪽(알림+활동피드) ---
        left, right = st.columns([2, 1])

        with left:
            with st.container(border=True):
                hcol1, hcol2 = st.columns([4, 1])
                with hcol1:
                    st.markdown("<p style='font-size:0.8rem;font-weight:600;margin:0'>월별 GMV 추이</p>", unsafe_allow_html=True)
                with hcol2:
                    if st.button("상세 보기", icon=":material/arrow_forward:", icon_position="right", type="tertiary", key="link_dashboard"):
                        st.switch_page("dashboard/page.py")

                cur = currency_config()
                monthly = orders.set_index("order_date").resample("MS")["total_amount"].sum() / cur["scale"]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=[f"{d.month}월" for d in monthly.index],
                    y=monthly.values,
                    mode="lines+markers",
                    line=dict(color=PURPLE, width=2),
                    marker=dict(size=5, color=PURPLE),
                    hovertemplate=f"%{{x}}<br>GMV {cur['symbol']}%{{y:.1f}}{cur['scale_label']}<extra></extra>",
                ))
                fig.update_layout(
                    height=150,
                    margin=dict(l=0, r=10, t=10, b=0),
                    plot_bgcolor="white",
                    paper_bgcolor="white",
                    font=dict(size=10, color="#888699"),
                    xaxis=dict(showgrid=False, showline=False),
                    yaxis=dict(showgrid=True, gridcolor="#F0EDFB", showline=False),
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

            with st.container(border=True):
                hcol1, hcol2 = st.columns([4, 1])
                with hcol1:
                    st.markdown("<p style='font-size:0.8rem;font-weight:600;margin:0'>최근 캠페인</p>", unsafe_allow_html=True)
                with hcol2:
                    if st.button("전체 보기", icon=":material/arrow_forward:", icon_position="right", type="tertiary", key="link_campaign"):
                        st.switch_page("automation/page.py")

                if history_df.empty:
                    st.caption("아직 발송 이력이 없습니다.")
                else:
                    rows_html = ""
                    for _, row in history_df.tail(3).iloc[::-1].iterrows():
                        rows_html += (
                            "<tr>"
                            f"<td style='font-family:monospace;white-space:nowrap'>{row.get('발송일시', '')}</td>"
                            f"<td><span class='segment-pill'>{row.get('세그먼트', '')}</span></td>"
                            f"<td>{row.get('대상 인원', '')}명</td>"
                            f"<td style='max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{row.get('메시지 요약', '')}</td>"
                            "</tr>"
                        )
                    st.markdown(
                        "<table class='mini-table'><thead><tr>"
                        "<th>발송일시</th><th>세그먼트</th><th>대상</th><th>메시지</th>"
                        f"</tr></thead><tbody>{rows_html}</tbody></table>",
                        unsafe_allow_html=True,
                    )

        with right:
            with st.container(border=True):
                st.markdown("<p style='font-size:0.8rem;font-weight:600;margin-bottom:6px'>최근 활동</p>", unsafe_allow_html=True)

                feed_items = []
                if not history_df.empty:
                    last_row = history_df.iloc[-1]
                    feed_items.append((
                        "primary",
                        f"{last_row.get('세그먼트', '')} 대상 캠페인 {last_row.get('상태', '')}",
                        last_row.get("발송일시", ""),
                        f"{last_row.get('대상 인원', '')}명",
                    ))
                if dormant_count > 0:
                    feed_items.append((
                        "amber",
                        f"휴면 고객 {dormant_count}명 — 60일 이상 미구매 상태",
                        "오늘",
                        "조치 필요",
                    ))
                new_signups = 0
                if "signup_date" in users.columns:
                    su = pd.to_datetime(users["signup_date"], errors="coerce")
                    new_signups = int((su >= latest_date - timedelta(days=1)).sum())
                if new_signups > 0:
                    feed_items.append((
                        "emerald",
                        f"신규 고객 {new_signups}명 유입",
                        "오늘",
                        "최근 24시간",
                    ))
                if next_scheduled is not None:
                    feed_items.append((
                        "primary",
                        f"{next_scheduled.get('세그먼트', '')} 대상 캠페인 예약됨",
                        str(next_scheduled["발송예정일시"]),
                        f"{next_scheduled_count}명 대상",
                    ))

                if not feed_items:
                    st.caption("최근 활동이 없습니다.")
                else:
                    for dot, text, time_label, meta in feed_items[:4]:
                        st.markdown(
                            f"<div class='activity-item'><span class='activity-dot dot-{dot}'></span>"
                            f"<div><div class='activity-text'>{text}</div>"
                            f"<div class='activity-meta'>{time_label} · {meta}</div></div></div>",
                            unsafe_allow_html=True,
                        )

                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)

    except FileNotFoundError:
        st.warning("데이터 파일을 찾을 수 없습니다. data/ 폴더를 확인해주세요.")


def _run_app():
    pg = st.navigation(
        [
            st.Page(render_home, title="홈", icon=":material/home:", default=True),
            st.Page("dashboard/page.py", title="대시보드", icon=":material/dashboard:", url_path="dashboard"),
            st.Page("ai_insights/page.py", title="고객 분석", icon=":material/group:", url_path="ai-insights"),
            st.Page("automation/page.py", title="자동화", icon=":material/bolt:", url_path="automation"),
        ]
    )
    pg.run()

    from ai_insights.chatbot import render_floating_chat
    render_floating_chat(force_open_once=st.session_state.pop("_force_open_ai_chat", False))


if SKIP_LOGIN:
    with st.sidebar:
        st.caption("🔓 로그인 생략 모드 (로컬 개발용)")
        dataset_source = st.selectbox(
            "데이터셋",
            options=list(DATASET_OPTIONS.keys()),
            format_func=lambda k: DATASET_OPTIONS[k],
            key="dev_dataset_select",
        )
    st.session_state["dataset_source"] = dataset_source
    st.session_state.setdefault("auth_company_name", DATASET_OPTIONS[dataset_source])
    _run_app()
elif not is_logged_in():
    if st.session_state.get("_auth_view") == "signup":
        render_signup_form()
    else:
        render_login_form()
elif st.session_state.get("_onboarding_keys"):
    render_onboarding_screen()
else:
    render_logout_button()
    _run_app()
