"""
담당자: 탭1 차트 담당
작업 내용: KPI 카드, 고객 프로필, GMV 추이, 세그먼트/카테고리/채널 랭킹, 퍼널, RFM/코호트(상세 분석)
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from utils.rfm import calculate_rfm, assign_segment
from utils.data_loader import load_data, fmt_amount, currency_config, get_dataset_source

ACCENT = "#7C3AED"
PALE_PURPLE = "#C4B5FD"
ORANGE = "#F97316"
MALE_COLOR = "#C4B5FD"
FEMALE_COLOR = "#7C3AED"
PURPLE_SCALE = ["#F5F5F8", "#EDE9FE", "#DDD6FE", "#C4B5FD", "#A78BFA", "#8B5CF6", "#7C3AED", "#5B21B6"]

GROUP_ICON_SVG = """
<svg width="40" height="40" viewBox="0 0 24 24" fill="{color}">
<circle cx="9" cy="8" r="3.4"/><path d="M2.5 20.5c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6v.8H2.5z"/>
<circle cx="17.5" cy="9" r="2.6" opacity="0.6"/>
<path d="M14.8 21.3v-.9c0-2.1.9-3.9 2.4-5 2.7.2 4.8 2.3 4.8 5.1v.8z" opacity="0.6"/>
</svg>
"""

AGE_ICON_SVG = """
<svg width="24" height="24" viewBox="0 0 24 24" fill="{color}">
<circle cx="4.5" cy="15" r="1.8"/><rect x="2.7" y="17.2" width="3.6" height="4.3" rx="1"/>
<circle cx="12" cy="10.5" r="2.4"/><rect x="9.3" y="13.3" width="5.4" height="8.2" rx="1.1"/>
<circle cx="19.2" cy="12.5" r="2.1"/><rect x="16.9" y="15" width="4.6" height="6.5" rx="1"/>
</svg>
"""

DASHBOARD_CSS = """
<style>
div[class*="st-key-card-"] {
    transition: box-shadow 0.15s ease, transform 0.15s ease, border-color 0.15s ease;
}
div[class*="st-key-card-"]:hover {
    border-color: #7C3AED !important;
    box-shadow: 0 6px 16px rgba(124, 58, 237, 0.18);
    transform: translateY(-2px);
}
/* 차트 제목 크기를 커스텀 랭킹 차트 제목과 동일하게 맞춰서 섹션 전체 제목 크기를 통일 */
div[data-testid="stHeadingWithActionElements"] h3 {
    font-size: 0.85rem !important;
    font-weight: 600 !important;
}
/* 사이드바 기간 필터의 "최근 7일" 등 라디오 옵션 글씨 크기 축소 */
section[data-testid="stSidebar"] div[role="radiogroup"] p {
    font-size: 13px !important;
}
/* KPI 카드 4개를 스크롤해도 항상 상단에 보이게 고정 */
div[class*="st-key-kpi-sticky-row"] {
    position: sticky;
    top: 0;
    z-index: 999;
    background: #FFFFFF;
    padding: 8px 0 4px 0;
}
</style>
"""

SEGMENT_ORDER = ["VIP", "충성 고객", "이탈 위험", "휴면"]
SEGMENT_COLORS = {
    "VIP": "#7C3AED",
    "충성 고객": "#A78BFA",
    "이탈 위험": "#C4B5FD",
    "휴면": "#DDD6FE",
}

CHANNEL_KR = {
    "SNS": "SNS",
    "search_ad": "검색광고",
    "direct": "직접유입",
    "email": "이메일",
    "referral": "추천",
}

PERSONA_KR = {
    "new_explorer": "신규 탐색자",
    "impulsive_buyer": "충동 구매자",
    "discount_hunter": "할인 헌터",
    "brand_loyalist": "브랜드 충성 고객",
    "churn_risk": "이탈 위험 고객",
    "dormant": "휴면 고객",
}

AGE_LABELS = ["10대", "20대", "30대", "40대", "50대", "60대 이상"]


def _pct_delta(current, previous):
    """전기 대비 증감률(%). 전기 값이 0이면 0 반환"""
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


def _fmt_duration(minutes):
    """분 단위 소요시간을 사람이 읽기 좋은 단위(분/시간/일)로 표시"""
    if pd.isna(minutes):
        return "-"
    if minutes >= 1440:
        return f"{minutes / 1440:.1f}일"
    if minutes >= 60:
        return f"{minutes / 60:.1f}시간"
    return f"{minutes:.0f}분"


def compute_kpis(cur_orders, cur_events, prev_orders, prev_events):
    """KPI 3종 + 선택 기간을 직전 동일 길이 기간과 비교한 증감률"""
    gmv_this = cur_orders["total_amount"].sum()
    gmv_last = prev_orders["total_amount"].sum()

    aov_this = cur_orders["total_amount"].mean() if len(cur_orders) else 0
    aov_last = prev_orders["total_amount"].mean() if len(prev_orders) else 0

    sessions_this = cur_events["session_id"].nunique()
    sessions_last = prev_events["session_id"].nunique()
    buy_sessions_this = cur_events.loc[cur_events["event_type"] == "purchase", "session_id"].nunique()
    buy_sessions_last = prev_events.loc[prev_events["event_type"] == "purchase", "session_id"].nunique()
    conv_this = (buy_sessions_this / sessions_this * 100) if sessions_this else 0
    conv_last = (buy_sessions_last / sessions_last * 100) if sessions_last else 0

    return {
        "gmv": (gmv_this, _pct_delta(gmv_this, gmv_last)),
        "aov": (aov_this, _pct_delta(aov_this, aov_last)),
        "conversion": (conv_this, conv_this - conv_last),
    }


def _kpi_card(label, value_text, delta, unit="%", key=None, ask_question=None):
    """전월대비 배지가 포함된 텍스트형 KPI 카드. ask_question을 주면 카드 안에 별도
    버튼을 두는 대신, 카드 전체를 눌렀을 때 플로팅 챗봇이 열리면서 그 질문으로 바로
    분석해준다 — ai_insights/chatbot.py의 AI_CHAT_CSS가 "click_" 접두사 컨테이너 안의
    "ask_" 접두사 버튼을 투명하게 카드 전체 크기로 늘려주는 방식이라, 컨테이너/버튼
    key를 그 접두사 규칙에 맞춰주기만 하면 카드 어디를 눌러도 그 버튼이 눌린 것처럼
    동작한다(이 파일에서 직접 CSS를 추가할 필요는 없음)."""
    if delta > 0:
        cls, arrow = "trend-up", "▲"
    elif delta < 0:
        cls, arrow = "trend-down", "▼"
    else:
        cls, arrow = "trend-neutral", ""
    trend_html = f"<span class='trend-pill {cls}'>{arrow} {abs(delta):.1f}{unit}</span>"
    container_key = f"click_{key}" if (ask_question and key) else key
    with st.container(border=True, key=container_key):
        st.markdown(
            f"<div class='stat-card-top'><span class='stat-label'>{label}</span>{trend_html}</div>"
            f"<div class='stat-value'>{value_text}</div>"
            f"<div class='stat-sub'>이전 기간 대비</div>",
            unsafe_allow_html=True,
        )
        if ask_question and st.button("이 지표 분석하기", key=f"ask_{key}"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot(ask_question)


def render_kpi_cards(orders, events, prev_orders, prev_events):
    """KPI 카드 3개 (GMV / AOV / 구매 전환율)"""
    kpi = compute_kpis(orders, events, prev_orders, prev_events)

    row = st.columns(3)
    with row[0]:
        v, d = kpi["gmv"]
        _kpi_card("GMV", fmt_amount(v), d, key="card-kpi-gmv", ask_question="GMV 변화 원인을 분석해줘")
    with row[1]:
        v, d = kpi["aov"]
        _kpi_card("AOV", fmt_amount(v, scaled=False), d, key="card-kpi-aov", ask_question="평균 주문 금액(AOV) 변화를 분석해줘")
    with row[2]:
        v, d = kpi["conversion"]
        _kpi_card("구매 전환율", f"{v:.1f}%", d, unit="%p", key="card-kpi-conv", ask_question="구매 전환율 변화 원인을 분석해줘")


def render_gmv_chart(orders, granularity="월별"):
    """GMV & 주문 수 추이 콤보 차트 (상단 전역 필터의 기간/단위를 그대로 사용, 최신 구간만 강조색).
    이 차트는 마우스 오버 툴팁(구간별 정확한 값 확인)이 유용해서, 카드 전체 클릭 대신
    제목 옆에 작은 '분석하기' 버튼만 남겨 차트 자체의 hover/확대 기능은 그대로 살려둔다."""
    if orders.empty:
        with st.container(border=True):
            st.subheader(
                "GMV & 주문 수 추이",
                help="막대는 구간별 GMV(총매출), 선은 주문 건수예요. 진한 보라 막대는 가장 최근 구간이고 나머지는 이전 구간이에요.",
            )
            st.info("선택한 기간에 데이터가 없습니다.")
        return

    freq = {"일별": "D", "주별": "W", "월별": "ME"}[granularity]
    label_fmt = {"일별": "%m/%d", "주별": "%m/%d", "월별": "%Y-%m"}[granularity]
    currency_symbol = currency_config()["symbol"]

    grouped = (
        orders.set_index("order_date")
        .resample(freq)
        .agg(gmv=("total_amount", "sum"), order_count=("order_id", "count"))
        .reset_index()
    )
    grouped["label"] = grouped["order_date"].dt.strftime(label_fmt)
    bar_colors = [ACCENT if i == len(grouped) - 1 else PALE_PURPLE for i in range(len(grouped))]

    with st.container(border=True):
        col_title, col_btn = st.columns([6, 1])
        with col_title:
            st.subheader(
                "GMV & 주문 수 추이",
                help="막대는 구간별 GMV(총매출), 선은 주문 건수예요. 진한 보라 막대는 가장 최근 구간이고 나머지는 이전 구간이에요.",
            )
        with col_btn:
            if st.button("🤖", key="ask-chart-gmv-trend", help="이 차트를 AI에게 분석해달라고 요청"):
                from ai_insights.chatbot import ask_chatbot
                ask_chatbot("GMV와 주문 수 추이를 분석해줘")

        fig = go.Figure()
        fig.add_bar(
            x=grouped["label"], y=grouped["gmv"], name="GMV", marker_color=bar_colors,
            hovertemplate="%{x}<br>GMV " + currency_symbol + "%{y:,.0f}<extra></extra>",
        )
        fig.add_trace(
            go.Scatter(
                x=grouped["label"],
                y=grouped["order_count"],
                name="주문 수",
                mode="lines+markers",
                yaxis="y2",
                line=dict(color=ORANGE, width=2, dash="dash"),
                marker=dict(size=6, color=ORANGE),
                hovertemplate="%{x}<br>주문 %{y:,}건<extra></extra>",
            )
        )
        fig.update_layout(
            height=260,
            yaxis=dict(title=f"GMV ({currency_symbol})", gridcolor="#F0EDFB"),
            yaxis2=dict(title="주문 수", overlaying="y", side="right"),
            xaxis=dict(showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
            margin=dict(t=30),
            plot_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified",
        )
        st.plotly_chart(fig, width='stretch')


def _mask_name(value):
    """고객 실명을 첫 글자만 남기고 마스킹 (예: 김민수 -> 김*수, 이수 -> 이*)."""
    if not isinstance(value, str) or not value:
        return value
    if len(value) == 1:
        return "*"
    if len(value) == 2:
        return value[0] + "*"
    return value[0] + "*" * (len(value) - 2) + value[-1]


def _mask_sensitive(df):
    """원본 데이터 팝오버에 실제 고객 이름이 그대로 노출되지 않도록 마스킹한 사본을 반환."""
    if "name" not in df.columns:
        return df
    df = df.copy()
    df["name"] = df["name"].map(_mask_name)
    return df


def _data_view_button(data, label="원본 데이터 보기"):
    """탭 상단에 배치하는 원본 데이터 보기 버튼. data는 DataFrame 하나 또는 {구분 라벨: DataFrame} 형태로 여러 개 전달 가능
    (팝오버로 뜨는 표 우측 상단 메뉴에서 CSV 다운로드도 가능). 팝오버 자체는 버튼 크기에 맞춰 작게 시작하지만,
    안의 표에 고정 픽셀 너비를 줘서 옆으로 드래그하지 않고도 여러 컬럼이 한눈에 보이도록 넓게 펼침.
    고객 실명(name 컬럼)은 민감정보라 표시 전에 마스킹한다."""
    with st.popover(label):
        if isinstance(data, dict):
            for i, (name, df) in enumerate(data.items()):
                if i > 0:
                    st.markdown("---")
                st.caption(name)
                st.dataframe(_mask_sensitive(df), width=850, hide_index=True)
        else:
            st.dataframe(_mask_sensitive(data), width=850, hide_index=True)


def _render_ranked_bars(title, series, colors=None, value_fmt=None, icon=None, ask_question=None, key=None):
    """랭킹형 가로 막대 리스트 (라벨-막대-값 순, 값 큰 순 정렬). colors 미지정 시 1위만 강조색.
    ask_question과 key를 같이 주면 카드 전체가 테두리로 감싸지고, 카드 아무 곳이나 클릭해도
    그 질문으로 플로팅 챗봇이 열린다 (KPI 카드와 동일한 click_/ask_ 접두사 컨벤션 재사용,
    hover로 값 보는 기능은 카드를 누르는 순간 대신 잃게 되는 트레이드오프를 감수함)."""
    container = st.container(border=True, key=f"click_{key}") if (ask_question and key) else st.container()
    with container:
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:4px'>{icon or ''}"
            f"<span style='font-size:0.85rem;font-weight:600'>{title}</span></div>",
            unsafe_allow_html=True,
        )

        if series.empty or series.sum() == 0:
            st.info("표시할 데이터가 없습니다.")
            return

        data = series.sort_values(ascending=False)
        if isinstance(colors, dict):
            bar_colors = [colors[k] for k in data.index]
        else:
            bar_colors = [ACCENT] + [PALE_PURPLE] * (len(data) - 1)
        data = data.iloc[::-1]
        bar_colors = bar_colors[::-1]

        fmt = value_fmt or fmt_amount
        text = [fmt(v) for v in data.values]

        fig = go.Figure(
            go.Bar(
                x=data.values,
                y=data.index.astype(str),
                orientation="h",
                marker_color=bar_colors,
                text=text,
                textposition="outside",
                cliponaxis=False,
                hovertemplate="%{y}: %{text}<extra></extra>",
            )
        )
        fig.update_layout(
            height=26 * len(data) + 34,
            margin=dict(l=10, r=70, t=10, b=10),
            xaxis=dict(visible=False, range=[0, data.values.max() * 1.2]),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width='stretch')

        if ask_question and key and st.button("이 차트 분석하기", key=f"ask_{key}"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot(ask_question)


def render_gender_split(users):
    """성별 분포 (도넛 차트 옆에 남녀 비율 텍스트를 나란히 배치). 카드 전체 클릭 시 챗봇 연동."""
    counts = users["gender"].value_counts()
    male = int(counts.get("M", 0))
    female = int(counts.get("F", 0))
    total = male + female
    if total == 0:
        st.info("표시할 데이터가 없습니다.")
        return

    with st.container(border=True, key="click_chart-gender"):
        st.markdown(
            "<div style='font-size:0.85rem;font-weight:600;margin-bottom:4px'>성별 분포</div>",
            unsafe_allow_html=True,
        )

        col_chart, col_labels = st.columns([1, 1])
        with col_chart:
            fig = go.Figure(
                go.Pie(
                    values=[male, female],
                    labels=["남성", "여성"],
                    hole=0.55,
                    rotation=90,
                    sort=False,
                    marker=dict(colors=[MALE_COLOR, FEMALE_COLOR], line=dict(color="white", width=2)),
                    textinfo="none",
                    hovertemplate="%{label}: %{value}명 (%{percent})<extra></extra>",
                    showlegend=False,
                )
            )
            fig.update_layout(height=130, margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
        with col_labels:
            st.markdown(
                "<div style='display:flex;flex-direction:column;justify-content:center;height:130px;gap:14px'>"
                f"<div><span style='color:{MALE_COLOR};font-weight:700'>남성</span><br>"
                f"<span style='font-size:1.3rem;font-weight:700'>{male:,}명</span> "
                f"<span style='color:#868E96'>({male / total * 100:.0f}%)</span></div>"
                f"<div><span style='color:{FEMALE_COLOR};font-weight:700'>여성</span><br>"
                f"<span style='font-size:1.3rem;font-weight:700'>{female:,}명</span> "
                f"<span style='color:#868E96'>({female / total * 100:.0f}%)</span></div>"
                "</div>",
                unsafe_allow_html=True,
            )

        if st.button("이 분포 분석하기", key="ask_chart-gender"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot("성별 분포를 분석해줘")


def render_age_brackets(users):
    """연령대 분포 (세로 랭킹형)"""
    age_group = pd.cut(users["age"], bins=[9, 19, 29, 39, 49, 59, 120], labels=AGE_LABELS)
    counts = age_group.value_counts().reindex(AGE_LABELS).fillna(0)
    _render_ranked_bars(
        "연령대 분포", counts,
        value_fmt=lambda v: f"{v:,.0f}명",
        icon=AGE_ICON_SVG.format(color=ACCENT),
        ask_question="연령대별 고객 분포를 분석해줘",
        key="ranked-age",
    )


def render_persona_ranking(users):
    """페르소나별 고객 수 랭킹"""
    counts = users["persona_type"].value_counts()
    counts.index = counts.index.map(PERSONA_KR)
    _render_ranked_bars(
        "페르소나별 고객 수", counts, value_fmt=lambda v: f"{v:,.0f}명",
        ask_question="페르소나별 고객 수를 분석해줘", key="ranked-persona",
    )


def render_customer_profile(users, events):
    """고객 프로필 섹션 (총 고객 수 · 성별 · 연령대 · 페르소나) - 선택한 기간에 활동한 고객만 집계"""
    st.subheader("고객 프로필")

    active_ids = events["user_id"].unique()
    users_f = users[users["user_id"].isin(active_ids)]

    if users_f.empty:
        st.info("선택한 기간에 활동한 고객이 없습니다.")
        return

    col_total, col_gender = st.columns([1, 1])

    with col_total:
        with st.container(border=True, key="card-total-customers"):
            st.markdown(
                f"<div style='text-align:center'>{GROUP_ICON_SVG.format(color=ACCENT)}</div>"
                f"<div style='text-align:center;color:var(--athlepa-muted-text);font-size:0.78rem;margin-top:6px'>기간 내 활동 고객 수</div>"
                f"<div style='text-align:center;font-size:1.5rem;font-weight:700;margin-top:4px'>{len(users_f):,}명</div>"
                f"<div style='text-align:center;color:var(--athlepa-muted-text);font-size:0.68rem;margin-top:4px'>구매 여부와 무관하게 방문 등 활동이 있었던 고객</div>",
                unsafe_allow_html=True,
            )
    with col_gender:
        render_gender_split(users_f)

    col_age, col_persona = st.columns(2)
    with col_age:
        render_age_brackets(users_f)
    with col_persona:
        render_persona_ranking(users_f)


def render_activity_kpis(events):
    """DAU/WAU/MAU — events 기준 일/주/월 활성 사용자 수. 각각 바로 직전 같은 길이
    기간과 비교한 증감률도 같이 보여준다."""
    if events.empty:
        return
    max_date = events["timestamp"].max()

    def _active(days_back, offset=0):
        end = (max_date - pd.Timedelta(days=offset)).date()
        start = end - pd.Timedelta(days=days_back - 1)
        window = events[(events["timestamp"].dt.date >= start) & (events["timestamp"].dt.date <= end)]
        return window["user_id"].nunique()

    dau, dau_prev = _active(1), _active(1, offset=1)
    wau, wau_prev = _active(7), _active(7, offset=7)
    mau, mau_prev = _active(30), _active(30, offset=30)

    st.markdown(
        "<div style='font-size:0.85rem;font-weight:600;margin:0 0 4px'>활성 사용자 지표</div>",
        unsafe_allow_html=True,
    )
    row = st.columns(3)
    with row[0]:
        _kpi_card("DAU (일간)", f"{dau:,}명", _pct_delta(dau, dau_prev), key="card-dau", ask_question="오늘 하루 활성 고객 수를 분석해줘")
    with row[1]:
        _kpi_card("WAU (주간)", f"{wau:,}명", _pct_delta(wau, wau_prev), key="card-wau", ask_question="최근 7일 활성 고객 수를 분석해줘")
    with row[2]:
        _kpi_card("MAU (월간)", f"{mau:,}명", _pct_delta(mau, mau_prev), key="card-mau", ask_question="최근 30일 활성 고객 수를 분석해줘")


def render_segment_ranking(orders):
    """세그먼트별 매출 기여도 랭킹 (VIP / 충성 / 이탈위험 / 휴면)"""
    rfm = assign_segment(calculate_rfm(orders.copy()))
    gmv_by_segment = rfm.groupby("segment")["Monetary"].sum().reindex(SEGMENT_ORDER).fillna(0)
    _render_ranked_bars(
        "세그먼트별 매출", gmv_by_segment, colors=SEGMENT_COLORS,
        ask_question="세그먼트별 매출을 분석해줘", key="ranked-segment",
    )


def render_category_ranking(orders):
    """카테고리별 매출 랭킹 (항목이 많아 세로 막대로 넓게 표시). 카드 전체 클릭 시 챗봇 연동."""
    with st.container(border=True, key="click_chart-category"):
        st.markdown(
            "<span style='font-size:0.85rem;font-weight:600'>카테고리별 매출</span>",
            unsafe_allow_html=True,
        )
        data = orders.groupby("category")["total_amount"].sum().sort_values(ascending=False)
        if data.empty or data.sum() == 0:
            st.info("표시할 데이터가 없습니다.")
            return

        bar_colors = [ACCENT] + [PALE_PURPLE] * (len(data) - 1)
        text = [fmt_amount(v) for v in data.values]

        fig = go.Figure(
            go.Bar(
                x=data.index.astype(str),
                y=data.values,
                marker_color=bar_colors,
                text=text,
                textposition="outside",
                cliponaxis=False,
                hovertemplate="%{x}: %{text}<extra></extra>",
            )
        )
        fig.update_layout(
            height=230,
            margin=dict(l=10, r=10, t=20, b=10),
            yaxis=dict(visible=False, range=[0, data.values.max() * 1.2]),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width='stretch')

        if st.button("이 차트 분석하기", key="ask_chart-category"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot("카테고리별 매출을 분석해줘")


def render_channel_ranking(users, orders):
    """유입 채널별 매출 랭킹"""
    merged = orders.merge(users[["user_id", "acquisition_channel"]], on="user_id")
    gmv_by_channel = merged.groupby("acquisition_channel")["total_amount"].sum()
    gmv_by_channel.index = gmv_by_channel.index.map(CHANNEL_KR)
    _render_ranked_bars(
        "유입 채널별 매출", gmv_by_channel,
        ask_question="채널별 매출을 분석해줘", key="ranked-channel",
    )


def render_rfm_scatter(orders):
    """RFM 산포도 (버블 차트) - x:구매빈도, y:구매금액, color:최근성. 이 차트는 '점에 마우스를
    올리면 고객별 상세 정보'라는 hover 기능이 핵심 기능이라, 카드 전체 클릭 대신 제목 옆에
    작은 '분석하기' 버튼만 남겨 hover는 그대로 살려둔다."""
    rfm = assign_segment(calculate_rfm(orders.copy()))

    with st.container(border=True):
        col_title, col_btn = st.columns([6, 1])
        with col_title:
            st.subheader(
                "RFM 산포도",
                help=(
                    "RFM은 고객을 최근성(Recency)·구매빈도(Frequency)·구매금액(Monetary) "
                    "세 지표로 분류하는 방법이에요.\n\n"
                    "- **X축**: 구매 빈도 (많을수록 오른쪽)\n"
                    "- **Y축**: 구매 금액 (클수록 위쪽)\n"
                    "- **색상**: 마지막 구매 후 지난 일수 (연할수록 최근, 진할수록 오래됨)\n"
                    "- **점 크기**: 구매 금액에 비례\n\n"
                    "점에 마우스를 올리면 고객별 상세 정보도 볼 수 있어요."
                ),
            )
        with col_btn:
            if st.button("🤖", key="ask-chart-rfm", help="이 차트를 AI에게 분석해달라고 요청"):
                from ai_insights.chatbot import ask_chatbot
                ask_chatbot("RFM 산포도를 분석해줘")

        fig = px.scatter(
            rfm,
            x="Frequency",
            y="Monetary",
            color="Recency",
            size="Monetary",
            hover_data=["user_id", "segment"],
            color_continuous_scale=[PALE_PURPLE, ACCENT],
            labels={"Frequency": "구매 빈도", "Monetary": "구매 금액", "Recency": "최근성(일)"},
        )
        symbol = currency_config()["symbol"]
        fig.update_traces(
            marker=dict(opacity=0.75, line=dict(width=0)),
            hovertemplate=(
                "<b>고객 %{customdata[0]}</b> · %{customdata[1]}<br>"
                "최근 구매: %{marker.color}일 전<br>"
                "구매 빈도: %{x}회<br>"
                f"구매 금액: {symbol}" + "%{y:,.0f}"
                "<extra></extra>"
            ),
        )
        fig.update_layout(
            height=320,
            margin=dict(t=30),
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="#F0EDFB"),
            yaxis=dict(gridcolor="#F0EDFB"),
        )
        st.plotly_chart(fig, width='stretch')


def _render_funnel_bars(labels, values, color):
    """단계별 도달 인원을 깔때기(funnel) 도형으로 표시 (Amazon PM 피드백 반영 —
    이전엔 진행 바 목록이었는데, 각 단계가 첫 단계 대비 얼마나 좁아지는지 한눈에
    보이도록 실제 깔때기 모양으로 교체)"""
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=values,
            textposition="inside",
            textinfo="value+percent initial",
            marker=dict(color=color),
            connector=dict(line=dict(color=PALE_PURPLE, width=1)),
        )
    )
    fig.update_layout(
        height=70 * len(labels) + 40,
        margin=dict(l=10, r=10, t=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(size=12, color="#111018"),
    )
    st.plotly_chart(fig, width='stretch')


def render_funnel(events):
    """구매 퍼널 (방문 → 상품조회 → 장바구니 → 구매). 각 단계를 최초 방문자 수 대비
    도달 비율만큼 채운 진행 바로 표시. 카드 전체 클릭 시 챗봇 연동."""
    stages = [
        ("page_view", "방문"),
        ("product_view", "상품조회"),
        ("add_to_cart", "장바구니"),
        ("purchase", "구매"),
    ]
    values = [events.loc[events["event_type"] == key, "session_id"].nunique() for key, _ in stages]
    labels = [label for _, label in stages]

    with st.container(border=True, key="click_chart-funnel"):
        st.subheader("구매 퍼널")
        _render_funnel_bars(labels, values, ACCENT)

        # 단계 전환마다 전환율을 같이 보여줌 (세션 내 이벤트가 같은 시각으로 기록돼 있어 소요시간은 의미가 없어 제외)
        rows_html = "".join(
            f"<div class='summary-row'><span class='summary-label'>{stages[i - 1][1]} → {stages[i][1]}</span>"
            f"<span class='summary-value'>{(values[i] / values[i - 1] * 100) if values[i - 1] else 0.0:.1f}%</span></div>"
            for i in range(1, len(stages))
        )
        st.markdown(f"<div style='margin-top:16px'>{rows_html}</div>", unsafe_allow_html=True)

        if st.button("이 차트 분석하기", key="ask_chart-funnel"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot("구매 퍼널을 분석해줘")


def render_repeat_funnel(users, orders, start, end):
    """회원가입 → 첫 구매 → 재구매 퍼널. '기간 필터'에서 고른 기간에 가입한 고객 코호트를 기준으로
    (가입일이 그 기간에 속하는 고객만), 전체 주문 이력에서 첫 구매·재구매까지 도달했는지 추적"""
    st.subheader(
        "회원가입 → 첫 구매 → 재구매",
        help=f"기간 필터에서 고른 가입일({start} ~ {end}) 기준 고객 코호트를, 이후 전체 주문 이력으로 추적한 결과예요.",
    )

    cohort_users = users[(users["signup_date"].dt.date >= start) & (users["signup_date"].dt.date <= end)]
    cohort_ids = set(cohort_users["user_id"])
    if not cohort_ids:
        st.info("선택한 기간에 가입한 고객이 없습니다.")
        return

    order_lists = (
        orders[orders["user_id"].isin(cohort_ids)]
        .sort_values("order_date")
        .groupby("user_id")["order_date"]
        .apply(list)
    )
    first_purchase_ids = set(order_lists.index)
    repeat_ids = {uid for uid, dates in order_lists.items() if len(dates) >= 2}

    labels = ["회원가입", "첫 구매", "재구매"]
    values = [len(cohort_ids), len(first_purchase_ids), len(repeat_ids)]

    _render_funnel_bars(labels, values, ORANGE)

    # 회원가입 -> 첫 구매, 첫 구매 -> 재구매 전환율 및 소요시간(평균/중앙값)
    signup_dates = cohort_users.set_index("user_id")["signup_date"]

    to_first = [
        (order_lists[uid][0] - signup_dates[uid]).total_seconds() / 60
        for uid in first_purchase_ids
        if (order_lists[uid][0] - signup_dates[uid]).total_seconds() >= 0
    ]
    to_repeat = [
        (order_lists[uid][1] - order_lists[uid][0]).total_seconds() / 60
        for uid in repeat_ids
        if (order_lists[uid][1] - order_lists[uid][0]).total_seconds() >= 0
    ]
    to_first = pd.Series(to_first, dtype=float)
    to_repeat = pd.Series(to_repeat, dtype=float)

    rows = [
        {
            "단계": "회원가입 → 첫 구매",
            "전환율": f"{(values[1] / values[0] * 100) if values[0] else 0:.1f}%",
            "평균": _fmt_duration(to_first.mean() if not to_first.empty else float("nan")),
            "중앙값": _fmt_duration(to_first.median() if not to_first.empty else float("nan")),
        },
        {
            "단계": "첫 구매 → 재구매",
            "전환율": f"{(values[2] / values[1] * 100) if values[1] else 0:.1f}%",
            "평균": _fmt_duration(to_repeat.mean() if not to_repeat.empty else float("nan")),
            "중앙값": _fmt_duration(to_repeat.median() if not to_repeat.empty else float("nan")),
        },
    ]
    rows_html = "".join(
        f"<div class='summary-row'><span class='summary-label'>{r['단계']}</span>"
        f"<span><span class='summary-value'>{r['전환율']}</span>"
        f"<span style='color:var(--athlepa-muted-text);font-size:11px;margin-left:10px'>"
        f"평균 {r['평균']} · 중앙값 {r['중앙값']}</span></span></div>"
        for r in rows
    )
    st.markdown(f"<div style='margin-top:16px'>{rows_html}</div>", unsafe_allow_html=True)


def render_cohort(users, orders_full, start, end):
    """재구매 유지율 (가입 월별 → 이후 N개월 재구매율).

    코호트(어느 가입월을 보여줄지)는 선택한 기간(start~end)에 가입한 고객으로 한정하지만,
    실제로 재구매했는지는 기간과 무관하게 orders_full(전체 주문 이력)로 판단해야 한다.
    기간으로 잘라낸 주문만 보면 '아직 그 개월차에 도달하지 않음'과 '실제로 재구매하지
    않음'을 구분할 수 없어서, 짧은 기간을 고를수록 재구매율이 실제보다 낮게(심하면 0%로)
    잘못 계산된다."""
    cohort_help = "가입 월(행)별 고객이 이후 몇 개월 차(열)에 다시 구매했는지 보여줘요. 색이 진할수록 재구매율이 높다는 뜻이고, 빈 칸은 아직 그 시점에 도달하지 않아 데이터가 없는 구간이에요."

    signup_month = users.set_index("user_id")["signup_date"].dt.to_period("M")
    start_month = pd.Timestamp(start).to_period("M")
    end_month = pd.Timestamp(end).to_period("M")
    cohort_month = signup_month[(signup_month >= start_month) & (signup_month <= end_month)]

    if cohort_month.empty:
        st.subheader("재구매 유지율", help=cohort_help)
        st.info("선택한 기간에 신규 가입한 고객이 없습니다.")
        return

    order_month = orders_full["order_date"].dt.to_period("M")
    merged = orders_full.assign(
        cohort_month=orders_full["user_id"].map(cohort_month),
        order_month=order_month,
    ).dropna(subset=["cohort_month"])

    if merged.empty:
        st.subheader("재구매 유지율", help=cohort_help)
        st.info("선택한 기간에 가입한 고객의 구매 이력이 아직 없습니다.")
        return

    # Period끼리 직접 빼면 데이터가 적을 때 NaT 처리 관련 오류가 나서, 연/월 정수로 직접 계산
    merged["cohort_month"] = merged["cohort_month"].astype("period[M]")
    merged["month_index"] = (
        (merged["order_month"].dt.year - merged["cohort_month"].dt.year) * 12
        + (merged["order_month"].dt.month - merged["cohort_month"].dt.month)
    )
    merged = merged[merged["month_index"] >= 0]

    cohort_sizes = cohort_month.value_counts()

    pivot = (
        merged.groupby(["cohort_month", "month_index"])["user_id"]
        .nunique()
        .reset_index()
        .pivot(index="cohort_month", columns="month_index", values="user_id")
        .sort_index()
    )
    retention = pivot.divide(cohort_sizes, axis=0) * 100
    text_matrix = [
        ["" if pd.isna(v) else f"{v:.1f}%" for v in row]
        for row in retention.values
    ]

    with st.container(border=True, key="click_chart-cohort"):
        st.subheader("재구매 유지율", help=cohort_help)

        fig = go.Figure(
            go.Heatmap(
                z=retention.values,
                x=[f"{i}개월차" for i in retention.columns],
                y=[str(i) for i in retention.index],
                colorscale=[[i / (len(PURPLE_SCALE) - 1), c] for i, c in enumerate(PURPLE_SCALE)],
                text=text_matrix,
                texttemplate="%{text}",
                textfont=dict(size=12),
                hoverongaps=False,
                showscale=False,
                xgap=4,
                ygap=4,
            )
        )
        fig.update_layout(
            height=max(220, 32 * len(retention.index) + 60),
            margin=dict(t=30),
            # 코호트가 1개뿐이면 "2026-06" 같은 축 라벨을 Plotly가 날짜로 오인해서 초 단위
            # 눈금을 그리는 경우가 있어, 항상 카테고리(문자열) 축으로 명시해서 막는다.
            xaxis=dict(type="category"),
            yaxis=dict(type="category"),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width='stretch')

        st.markdown(
            "<div style='display:flex;align-items:center;gap:8px;margin-top:-8px'>"
            "<span style='font-size:11px;color:var(--athlepa-muted-text)'>낮음</span>"
            f"<div style='flex:1;height:8px;border-radius:4px;background:linear-gradient(to right,{','.join(PURPLE_SCALE)})'></div>"
            "<span style='font-size:11px;color:var(--athlepa-muted-text)'>높음</span></div>",
            unsafe_allow_html=True,
        )

        if st.button("이 차트 분석하기", key="ask_chart-cohort"):
            from ai_insights.chatbot import ask_chatbot
            ask_chatbot("재구매 유지율을 분석해줘")


DATE_PRESETS = {
    "최근 7일": ("일별", 7),
    "최근 4주": ("주별", 28),
    "최근 3개월": ("월별", 90),
    "전체 기간": ("월별", None),
}


def render_date_filter(orders, events):
    """대시보드 전역 기간/단위 필터. 사이드바에 배치해서 본문을 넓게 씀 (KPI·매출·퍼널·추이 차트에 공통 적용)

    events가 비어있는 데이터셋(예: 행동 로그가 없는 데이콘 데이터)은 orders의 주문일 범위로
    대체한다. 둘 다 비어있으면 필터를 그릴 기준 자체가 없다는 뜻이라 None을 반환한다."""
    if not events.empty:
        min_date = events["timestamp"].min().date()
        max_date = events["timestamp"].max().date()
    elif not orders.empty:
        min_date = orders["order_date"].min().date()
        max_date = orders["order_date"].max().date()
    else:
        st.sidebar.warning("표시할 데이터가 없습니다.")
        return None, None, None, None, "일별", None, None, None

    with st.sidebar:
        st.markdown(
            "<div style='font-size:13px;font-weight:600;margin-bottom:0.5rem;color:#868E96'>기간 필터</div>",
            unsafe_allow_html=True,
        )
        options = list(DATE_PRESETS.keys()) + ["직접 선택"]
        # index와 key를 함께 넘기면 위젯이 처음 만들어질 때는 index가 기본값으로 쓰이지만,
        # 이미 session_state에 값이 있는데도 index를 같이 넘기면 Streamlit이 경고를 띄우므로
        # 이미 값이 있을 때는 index를 아예 넘기지 않는다.
        radio_kwargs = {} if "date_preset" in st.session_state else {"index": options.index("전체 기간")}
        choice = st.radio("빠른 선택", options, key="date_preset", **radio_kwargs)

        if choice == "직접 선택":
            date_source = events["timestamp"] if not events.empty else orders["order_date"]
            available_dates = sorted(date_source.dt.date.unique())
            start_kwargs = {} if "custom_start_date" in st.session_state else {"index": 0}
            end_kwargs = {} if "custom_end_date" in st.session_state else {"index": len(available_dates) - 1}
            start = st.selectbox("시작일", available_dates, key="custom_start_date", **start_kwargs)
            end = st.selectbox("종료일", available_dates, key="custom_end_date", **end_kwargs)
            if start > end:
                st.warning("시작일이 종료일보다 늦을 수 없습니다.")
                return None, None, None, None, "일별", choice, None, None
            granularity = "일별"  # 직접 선택은 고른 기간을 그대로 일 단위로 보여줌 (임의로 요약하지 않음)
        else:
            granularity, days_back = DATE_PRESETS[choice]
            if days_back:
                start = max(min_date, (pd.Timestamp(max_date) - pd.Timedelta(days=days_back - 1)).date())
            else:
                start = min_date
            end = max_date
        st.caption(f"{start} ~ {end}")

    # KPI 비교 기준: 선택 기간 바로 직전의 '같은 길이' 기간 (예: 최근 7일이면 그 이전 7일)
    span_days = (end - start).days + 1
    prev_end = (pd.Timestamp(start) - pd.Timedelta(days=1))
    prev_start = (prev_end - pd.Timedelta(days=span_days - 1)).date()
    prev_end = prev_end.date()

    f_orders = orders[(orders["order_date"].dt.date >= start) & (orders["order_date"].dt.date <= end)]
    f_events = events[(events["timestamp"].dt.date >= start) & (events["timestamp"].dt.date <= end)]
    prev_orders = orders[(orders["order_date"].dt.date >= prev_start) & (orders["order_date"].dt.date <= prev_end)]
    prev_events = events[(events["timestamp"].dt.date >= prev_start) & (events["timestamp"].dt.date <= prev_end)]
    return f_orders, f_events, prev_orders, prev_events, granularity, choice, start, end


def render_charts():
    """메인 렌더 함수 - Dashboard.py에서 호출. 어느 데이터셋을 보여줄지는 로그인한
    회사 계정 기준으로 이미 session_state["dataset_source"]에 정해져 있다(utils/auth.py)."""
    dataset_source = get_dataset_source()
    users, orders, events = load_data(dataset_source)

    if users.empty and orders.empty:
        st.warning("표시할 데이터가 없습니다.")
        return

    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    orders_f, events_f, prev_orders, prev_events, granularity, date_choice, filter_start, filter_end = render_date_filter(
        orders, events
    )
    if orders_f is None:
        return
    if orders_f.empty:
        st.warning("선택한 기간에 데이터가 없습니다.")
        return
    if events_f.empty:
        st.caption(":material/info: 이 데이터셋은 방문·행동 로그가 없어 일부 차트(행동 분석)는 비어 보일 수 있어요.")

    rfm = assign_segment(calculate_rfm(orders_f.copy()))

    # KPI 카드는 탭 선택창 위(공용 영역)에 두고 sticky 처리해서, 탭을 넘나들거나
    # 스크롤해도 항상 보이게 한다. 활성 사용자 지표(DAU/WAU/MAU)가 맨 위, 그 아래
    # GMV/AOV/전환율 카드 순서.
    with st.container(key="kpi-sticky-row"):
        render_activity_kpis(events_f)
        render_kpi_cards(orders_f, events_f, prev_orders, prev_events)

    tab_overview, tab_revenue, tab_behavior, tab_detail = st.tabs(
        ["개요", "매출 분석", "행동 분석", "상세 분석"]
    )

    with tab_overview:
        active_ids = events_f["user_id"].unique()
        users_active = users[users["user_id"].isin(active_ids)]
        profile_cols = [c for c in ["user_id", "name", "gender", "age"] if c in users_active.columns]
        profile_df = users_active[profile_cols]
        if "persona_type" in users_active.columns:
            profile_df = profile_df.assign(페르소나=users_active["persona_type"].map(PERSONA_KR).values)
        order_cols = [c for c in ["order_id", "order_date", "user_id", "category", "total_amount"] if c in orders_f.columns]

        _, btn_col = st.columns([6, 1.3])
        with btn_col:
            _data_view_button(
                {
                    "활동 고객 데이터": profile_df,
                    "주문 데이터": orders_f[order_cols].sort_values("order_date", ascending=False),
                }
            )

        render_customer_profile(users, events_f)

    with tab_revenue:
        merged_orders = orders_f.merge(users[["user_id", "acquisition_channel"]], on="user_id").merge(
            rfm[["user_id", "segment"]], on="user_id"
        )
        merged_orders["유입채널"] = merged_orders["acquisition_channel"].map(CHANNEL_KR)
        revenue_raw = merged_orders[
            ["order_id", "order_date", "user_id", "category", "total_amount", "유입채널", "segment"]
        ].sort_values("order_date", ascending=False)

        _, btn_col = st.columns([6, 1.3])
        with btn_col:
            _data_view_button(revenue_raw)

        col1, col2 = st.columns(2)
        with col1:
            render_segment_ranking(orders_f)
        with col2:
            render_channel_ranking(users, orders_f)

        col3, col4 = st.columns(2)
        with col3:
            render_category_ranking(orders_f)
        with col4:
            render_gmv_chart(orders_f, granularity)

    with tab_behavior:
        behavior_raw = events_f[["user_id", "session_id", "event_type", "timestamp"]].sort_values(
            "timestamp", ascending=False
        )
        cohort_signup_raw = users[
            (users["signup_date"].dt.date >= filter_start) & (users["signup_date"].dt.date <= filter_end)
        ]
        cohort_signup_cols = [c for c in ["user_id", "name", "gender", "age", "signup_date"] if c in cohort_signup_raw.columns]

        _, btn_col = st.columns([6, 1.3])
        with btn_col:
            _data_view_button(
                {
                    "이벤트 데이터 (구매 퍼널)": behavior_raw,
                    "가입 고객 데이터 (재구매 퍼널)": cohort_signup_raw[cohort_signup_cols],
                }
            )

        col_funnel_a, col_funnel_b = st.columns(2)
        with col_funnel_a:
            render_funnel(events_f)
        with col_funnel_b:
            render_repeat_funnel(users, orders, filter_start, filter_end)

    with tab_detail:
        # 코호트(어느 가입월을 볼지)는 선택한 기간에 가입한 고객으로 한정하되, 재구매 여부는
        # 기간과 무관하게 전체 주문 이력(orders)으로 판단한다. orders_f(기간으로 잘린 주문)를
        # 쓰면 "아직 그 개월차에 도달하지 않음"과 "실제로 재구매하지 않음"을 구분할 수 없어서,
        # 짧은 기간을 고를수록 재구매율이 실제보다 훨씬 낮게(심하면 0%로) 잘못 나온다.
        signup_month = users.set_index("user_id")["signup_date"].dt.to_period("M")
        start_month = pd.Timestamp(filter_start).to_period("M")
        end_month = pd.Timestamp(filter_end).to_period("M")
        cohort_month = signup_month[(signup_month >= start_month) & (signup_month <= end_month)]
        order_month = orders["order_date"].dt.to_period("M")
        cohort_merged = orders.assign(
            cohort_month=orders["user_id"].map(cohort_month), order_month=order_month
        ).dropna(subset=["cohort_month"])

        cohort_raw = pd.DataFrame()
        if not cohort_merged.empty:
            cohort_merged["cohort_month"] = cohort_merged["cohort_month"].astype("period[M]")
            cohort_merged["month_index"] = (
                (cohort_merged["order_month"].dt.year - cohort_merged["cohort_month"].dt.year) * 12
                + (cohort_merged["order_month"].dt.month - cohort_merged["cohort_month"].dt.month)
            )
            cohort_merged = cohort_merged[cohort_merged["month_index"] >= 0]
            cohort_raw = (
                cohort_merged.assign(
                    가입월=cohort_merged["cohort_month"].astype(str),
                    구매월=cohort_merged["order_month"].astype(str),
                )[["order_id", "user_id", "가입월", "구매월", "month_index", "category", "total_amount"]]
                .rename(columns={"month_index": "가입후개월차"})
                .sort_values("구매월", ascending=False)
            )

        _, btn_col = st.columns([6, 1.3])
        with btn_col:
            _data_view_button(
                {
                    "RFM (고객별 지표)": rfm[["user_id", "Recency", "Frequency", "Monetary", "segment"]],
                    "재구매 유지율 (주문별 가입월/구매월)": cohort_raw,
                }
            )

        col4, col5 = st.columns(2)
        with col4:
            render_rfm_scatter(orders_f)
        with col5:
            render_cohort(users, orders, filter_start, filter_end)