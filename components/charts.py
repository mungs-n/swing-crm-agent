"""
담당자: 탭1 차트 담당
작업 내용: KPI 카드, 고객 프로필, GMV 추이, 세그먼트/카테고리/채널 랭킹, 퍼널, RFM/코호트(상세 분석)
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

from utils.rfm import calculate_rfm, assign_segment

ACCENT = "#4C6EF5"
PALE_BLUE = "#BAC8FF"
POSITIVE = "#2F9E44"
NEGATIVE = "#E03131"
WARNING = "#E8590C"
MALE_COLOR = "#4C6EF5"
FEMALE_COLOR = "#F06595"

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
    border-color: #4C6EF5 !important;
    box-shadow: 0 6px 16px rgba(76, 110, 245, 0.18);
    transform: translateY(-2px);
}
/* 차트 제목 크기를 커스텀 랭킹 차트 제목(1.25rem)과 동일하게 맞춰서 섹션 전체 제목 크기를 통일 */
div[data-testid="stHeadingWithActionElements"] h3 {
    font-size: 1.25rem !important;
}
</style>
"""

SEGMENT_ORDER = ["VIP", "충성 고객", "이탈 위험", "휴면"]
SEGMENT_COLORS = {
    "VIP": "#4C6EF5",
    "충성 고객": "#66D9E8",
    "이탈 위험": "#FFA94D",
    "휴면": "#CED4DA",
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

@st.cache_data
def load_data():
    """데이터 로드 함수"""
    users = pd.read_csv("data/users.csv", parse_dates=["signup_date"])
    orders = pd.read_csv("data/orders.csv", parse_dates=["order_date"])
    events = pd.read_csv("data/events.csv", parse_dates=["timestamp"])
    return users, orders, events


def _pct_delta(current, previous):
    """전기 대비 증감률(%). 전기 값이 0이면 0 반환"""
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


def compute_kpis(cur_orders, cur_events, prev_orders, prev_events):
    """KPI 4종 + 선택 기간을 직전 동일 길이 기간과 비교한 증감률"""
    active_this = cur_events["user_id"].nunique()
    active_last = prev_events["user_id"].nunique()

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
        "active_users": (active_this, _pct_delta(active_this, active_last)),
        "gmv": (gmv_this, _pct_delta(gmv_this, gmv_last)),
        "aov": (aov_this, _pct_delta(aov_this, aov_last)),
        "conversion": (conv_this, conv_this - conv_last),
    }


def _kpi_card(label, value_text, delta, unit="%", key=None):
    """전월대비 배지가 포함된 텍스트형 KPI 카드"""
    color = POSITIVE if delta >= 0 else NEGATIVE
    arrow = "▲" if delta >= 0 else "▼"
    with st.container(border=True, key=key):
        st.markdown(
            f"<div style='color:#868E96;font-size:0.85rem'>{label}</div>"
            f"<div style='font-size:1.6rem;font-weight:700;margin-top:2px'>{value_text} "
            f"<span style='font-size:0.8rem;font-weight:600;color:{color}'>{arrow} {abs(delta):.1f}{unit}</span></div>"
            f"<div style='color:#ADB5BD;font-size:0.72rem'>이전 기간 대비</div>",
            unsafe_allow_html=True,
        )


def render_kpi_cards(orders, events, prev_orders, prev_events):
    """KPI 카드 4개 (GMV / AOV / 활성 고객 수 / 구매 전환율)"""
    kpi = compute_kpis(orders, events, prev_orders, prev_events)

    row = st.columns(4)
    with row[0]:
        v, d = kpi["gmv"]
        _kpi_card("GMV", f"₩{v / 1_000_000:.1f}M", d, key="card-kpi-gmv")
    with row[1]:
        v, d = kpi["aov"]
        _kpi_card("AOV", f"₩{v:,.0f}", d, key="card-kpi-aov")
    with row[2]:
        v, d = kpi["active_users"]
        _kpi_card("활성 고객 수", f"{v:,}명", d, key="card-kpi-active")
    with row[3]:
        v, d = kpi["conversion"]
        _kpi_card("구매 전환율", f"{v:.1f}%", d, unit="%p", key="card-kpi-conv")


def render_gmv_chart(orders, granularity="월별"):
    """GMV & 주문 수 추이 콤보 차트 (상단 전역 필터의 기간/단위를 그대로 사용, 최신 구간만 강조색)"""
    st.subheader(
        "GMV & 주문 수 추이",
        help="막대는 구간별 GMV(총매출), 선은 주문 건수예요. 진한 파란 막대는 가장 최근 구간이고 나머지는 이전 구간이에요.",
    )

    if orders.empty:
        st.info("선택한 기간에 데이터가 없습니다.")
        return

    freq = {"일별": "D", "주별": "W", "월별": "ME"}[granularity]
    label_fmt = {"일별": "%m/%d", "주별": "%m/%d", "월별": "%Y-%m"}[granularity]

    grouped = (
        orders.set_index("order_date")
        .resample(freq)
        .agg(gmv=("total_amount", "sum"), order_count=("order_id", "count"))
        .reset_index()
    )
    grouped["label"] = grouped["order_date"].dt.strftime(label_fmt)
    bar_colors = [ACCENT if i == len(grouped) - 1 else PALE_BLUE for i in range(len(grouped))]

    fig = go.Figure()
    fig.add_bar(
        x=grouped["label"], y=grouped["gmv"], name="GMV", marker_color=bar_colors,
        hovertemplate="%{x}<br>GMV ₩%{y:,.0f}<extra></extra>",
    )
    fig.add_trace(
        go.Scatter(
            x=grouped["label"],
            y=grouped["order_count"],
            name="주문 수",
            mode="lines+markers",
            yaxis="y2",
            line=dict(color="#FA5252"),
            hovertemplate="%{x}<br>주문 %{y:,}건<extra></extra>",
        )
    )
    fig.update_layout(
        yaxis=dict(title="GMV (원)"),
        yaxis2=dict(title="주문 수", overlaying="y", side="right"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=30),
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    st.plotly_chart(fig, width='stretch')


def _render_ranked_bars(title, series, colors=None, value_fmt=None, icon=None):
    """랭킹형 가로 막대 리스트 (라벨-막대-값 순, 값 큰 순 정렬). colors 미지정 시 1위만 강조색"""
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:4px'>{icon or ''}"
        f"<span style='font-size:1.25rem;font-weight:600'>{title}</span></div>",
        unsafe_allow_html=True,
    )

    if series.empty or series.sum() == 0:
        st.info("표시할 데이터가 없습니다.")
        return

    data = series.sort_values(ascending=False)
    if isinstance(colors, dict):
        bar_colors = [colors[k] for k in data.index]
    else:
        bar_colors = [ACCENT] + [PALE_BLUE] * (len(data) - 1)
    data = data.iloc[::-1]
    bar_colors = bar_colors[::-1]

    fmt = value_fmt or (lambda v: f"₩{v / 1_000_000:.1f}M")
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
        height=34 * len(data) + 50,
        margin=dict(l=10, r=70, t=10, b=10),
        xaxis=dict(visible=False, range=[0, data.values.max() * 1.2]),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width='stretch')


def render_gender_split(users):
    """성별 분포 (도넛 차트 옆에 남녀 비율 텍스트를 나란히 배치)"""
    counts = users["gender"].value_counts()
    male = int(counts.get("M", 0))
    female = int(counts.get("F", 0))
    total = male + female
    if total == 0:
        st.info("표시할 데이터가 없습니다.")
        return

    st.markdown(
        "<div style='font-size:1.25rem;font-weight:600;margin-bottom:4px'>성별 분포</div>",
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
        fig.update_layout(height=160, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
    with col_labels:
        st.markdown(
            "<div style='display:flex;flex-direction:column;justify-content:center;height:160px;gap:18px'>"
            f"<div><span style='color:{MALE_COLOR};font-weight:700'>남성</span><br>"
            f"<span style='font-size:1.3rem;font-weight:700'>{male:,}명</span> "
            f"<span style='color:#868E96'>({male / total * 100:.0f}%)</span></div>"
            f"<div><span style='color:{FEMALE_COLOR};font-weight:700'>여성</span><br>"
            f"<span style='font-size:1.3rem;font-weight:700'>{female:,}명</span> "
            f"<span style='color:#868E96'>({female / total * 100:.0f}%)</span></div>"
            "</div>",
            unsafe_allow_html=True,
        )


def render_age_brackets(users):
    """연령대 분포 (세로 랭킹형)"""
    age_group = pd.cut(users["age"], bins=[9, 19, 29, 39, 49, 59, 120], labels=AGE_LABELS)
    counts = age_group.value_counts().reindex(AGE_LABELS).fillna(0)
    _render_ranked_bars(
        "연령대 분포", counts,
        value_fmt=lambda v: f"{v:,.0f}명",
        icon=AGE_ICON_SVG.format(color=ACCENT),
    )


def render_persona_ranking(users):
    """페르소나별 고객 수 랭킹"""
    counts = users["persona_type"].value_counts()
    counts.index = counts.index.map(PERSONA_KR)
    _render_ranked_bars("페르소나별 고객 수", counts, value_fmt=lambda v: f"{v:,.0f}명")


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
                f"<div style='text-align:center;color:#868E96;font-size:0.85rem;margin-top:6px'>기간 내 활동 고객 수</div>"
                f"<div style='text-align:center;font-size:2rem;font-weight:800;margin-top:4px'>{len(users_f):,}명</div>"
                f"<div style='text-align:center;color:#ADB5BD;font-size:0.7rem;margin-top:4px'>구매 여부와 무관하게 방문 등 활동이 있었던 고객</div>",
                unsafe_allow_html=True,
            )
    with col_gender:
        render_gender_split(users_f)

    col_age, col_persona = st.columns(2)
    with col_age:
        render_age_brackets(users_f)
    with col_persona:
        render_persona_ranking(users_f)


def render_segment_ranking(orders):
    """세그먼트별 매출 기여도 랭킹 (VIP / 충성 / 이탈위험 / 휴면)"""
    rfm = assign_segment(calculate_rfm(orders.copy()))
    gmv_by_segment = rfm.groupby("segment")["Monetary"].sum().reindex(SEGMENT_ORDER).fillna(0)
    _render_ranked_bars("세그먼트별 매출", gmv_by_segment, colors=SEGMENT_COLORS)


def render_category_ranking(orders):
    """카테고리별 매출 랭킹 (항목이 많아 세로 막대로 넓게 표시)"""
    st.markdown(
        "<span style='font-size:1.25rem;font-weight:600'>카테고리별 매출</span>",
        unsafe_allow_html=True,
    )
    data = orders.groupby("category")["total_amount"].sum().sort_values(ascending=False)
    if data.empty or data.sum() == 0:
        st.info("표시할 데이터가 없습니다.")
        return

    bar_colors = [ACCENT] + [PALE_BLUE] * (len(data) - 1)
    text = [f"₩{v / 1_000_000:.1f}M" for v in data.values]

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
        height=320,
        margin=dict(l=10, r=10, t=20, b=10),
        yaxis=dict(visible=False, range=[0, data.values.max() * 1.2]),
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, width='stretch')


def render_channel_ranking(users, orders):
    """유입 채널별 매출 랭킹"""
    merged = orders.merge(users[["user_id", "acquisition_channel"]], on="user_id")
    gmv_by_channel = merged.groupby("acquisition_channel")["total_amount"].sum()
    gmv_by_channel.index = gmv_by_channel.index.map(CHANNEL_KR)
    _render_ranked_bars("유입 채널별 매출", gmv_by_channel)


def render_rfm_scatter(orders):
    """RFM 산포도 (버블 차트) - x:구매빈도, y:구매금액, color:최근성"""
    st.subheader("RFM 산포도")
    rfm = assign_segment(calculate_rfm(orders.copy()))

    fig = px.scatter(
        rfm,
        x="Frequency",
        y="Monetary",
        color="Recency",
        size="Monetary",
        hover_data=["user_id", "segment"],
        color_continuous_scale="RdYlBu",
        labels={"Frequency": "구매 빈도", "Monetary": "구매 금액", "Recency": "최근성(일)"},
    )
    fig.update_layout(margin=dict(t=30))
    st.plotly_chart(fig, width='stretch')


def render_funnel(events):
    """구매 퍼널 차트 (방문 → 상품조회 → 장바구니 → 구매)"""
    st.subheader("구매 퍼널")
    stages = [
        ("page_view", "방문"),
        ("product_view", "상품조회"),
        ("add_to_cart", "장바구니"),
        ("purchase", "구매"),
    ]
    values = [events.loc[events["event_type"] == key, "session_id"].nunique() for key, _ in stages]

    drop_offs = [0.0] + [
        (values[i - 1] - values[i]) / values[i - 1] if values[i - 1] else 0.0 for i in range(1, len(values))
    ]
    worst_idx = max(range(1, len(values)), key=lambda i: drop_offs[i])

    palette = ["#4C6EF5", "#748FFC", "#91A7FF", "#BAC8FF"]
    bar_colors = [WARNING if i == worst_idx else palette[i] for i in range(len(values))]

    fig = go.Figure(
        go.Funnel(
            y=[label for _, label in stages],
            x=values,
            texttemplate="%{value:,} (%{percentInitial})",
            marker=dict(color=bar_colors),
        )
    )
    fig.update_layout(margin=dict(t=30))
    st.plotly_chart(fig, width='stretch')


def render_cohort(users, orders):
    """코호트 리텐션 히트맵 (가입 월별 → 이후 N개월 재구매율). 분석 기간 이전 가입자는 코호트 정의가 안 되므로 제외"""
    st.subheader(
        "코호트 리텐션 히트맵",
        help="가입 월(행)별 고객이 이후 몇 개월 차(열)에 다시 구매했는지 보여줘요. 색이 진할수록 재구매율이 높다는 뜻이고, 빈 칸은 아직 그 시점에 도달하지 않아 데이터가 없는 구간이에요.",
    )

    if orders.empty:
        st.info("표시할 데이터가 없습니다.")
        return

    obs_start_month = orders["order_date"].min().to_period("M")
    signup_month = users.set_index("user_id")["signup_date"].dt.to_period("M")
    cohort_month = signup_month[signup_month >= obs_start_month]
    order_month = orders["order_date"].dt.to_period("M")

    merged = orders.assign(
        cohort_month=orders["user_id"].map(cohort_month),
        order_month=order_month,
    ).dropna(subset=["cohort_month"])

    if merged.empty:
        st.info("선택한 기간에 신규 가입 코호트 데이터가 없습니다.")
        return

    # Period끼리 직접 빼면 데이터가 적을 때 NaT 처리 관련 오류가 나서, 연/월 정수로 직접 계산
    merged["cohort_month"] = merged["cohort_month"].astype("period[M]")
    merged["month_index"] = (
        (merged["order_month"].dt.year - merged["cohort_month"].dt.year) * 12
        + (merged["order_month"].dt.month - merged["cohort_month"].dt.month)
    )

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

    fig = go.Figure(
        go.Heatmap(
            z=retention.values,
            x=[f"{i}개월차" for i in retention.columns],
            y=[str(i) for i in retention.index],
            colorscale="YlOrRd",
            text=text_matrix,
            texttemplate="%{text}",
            hoverongaps=False,
        )
    )
    fig.update_layout(margin=dict(t=30))
    st.plotly_chart(fig, width='stretch')


DATE_PRESETS = {
    "최근 7일": ("일별", 7),
    "최근 4주": ("주별", 28),
    "최근 3개월": ("월별", 90),
    "전체 기간": ("월별", None),
}


def render_date_filter(orders, events):
    """대시보드 전역 기간/단위 필터. 사이드바에 배치해서 본문을 넓게 씀 (KPI·매출·퍼널·추이 차트에 공통 적용)"""
    min_date = events["timestamp"].min().date()
    max_date = events["timestamp"].max().date()

    with st.sidebar:
        st.markdown("### 기간 필터")
        options = list(DATE_PRESETS.keys()) + ["직접 선택"]
        # index와 key를 함께 넘기면 위젯이 처음 만들어질 때는 index가 기본값으로 쓰이지만,
        # 이미 session_state에 값이 있는데도 index를 같이 넘기면 Streamlit이 경고를 띄우므로
        # 이미 값이 있을 때는 index를 아예 넘기지 않는다.
        radio_kwargs = {} if "date_preset" in st.session_state else {"index": options.index("전체 기간")}
        choice = st.radio("빠른 선택", options, key="date_preset", **radio_kwargs)

        if choice == "직접 선택":
            available_dates = sorted(events["timestamp"].dt.date.unique())
            start_kwargs = {} if "custom_start_date" in st.session_state else {"index": 0}
            end_kwargs = {} if "custom_end_date" in st.session_state else {"index": len(available_dates) - 1}
            start = st.selectbox("시작일", available_dates, key="custom_start_date", **start_kwargs)
            end = st.selectbox("종료일", available_dates, key="custom_end_date", **end_kwargs)
            if start > end:
                st.warning("시작일이 종료일보다 늦을 수 없습니다.")
                return None, None, None, None, "일별", choice
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
    return f_orders, f_events, prev_orders, prev_events, granularity, choice


def render_charts():
    """메인 렌더 함수 - Dashboard.py에서 호출"""
    try:
        users, orders, events = load_data()
    except FileNotFoundError:
        st.warning("데이터 파일을 찾을 수 없습니다. data/ 폴더에 CSV 파일을 넣어주세요.")
        return

    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

    orders_f, events_f, prev_orders, prev_events, granularity, date_choice = render_date_filter(orders, events)
    if orders_f is None:
        return
    if orders_f.empty or events_f.empty:
        st.warning("선택한 기간에 데이터가 없습니다.")
        return

    if st.button("🤖 이 기간으로 AI 인사이트 보기", type="primary"):
        # date_preset/custom_start_date/custom_end_date는 AI 인사이트 페이지와 동일한
        # key를 쓰고 있지만, Streamlit은 st.switch_page로 페이지를 이동하면 위젯에
        # 연결된 session_state 값을 초기화해버린다. 그래서 위젯 key가 아닌 별도의
        # "carry" key로 값을 직접 들고 넘어가서, AI 인사이트 페이지 쪽에서 위젯을
        # 만들기 전에 그 값을 다시 심어주는 방식으로 기간을 그대로 유지한다.
        st.session_state["ai_carry_date_preset"] = date_choice
        if date_choice == "직접 선택":
            st.session_state["ai_carry_custom_start"] = st.session_state.get("custom_start_date")
            st.session_state["ai_carry_custom_end"] = st.session_state.get("custom_end_date")
        st.session_state["ai_auto_trigger"] = True
        st.switch_page("pages/2_AI_Insights.py")

    render_kpi_cards(orders_f, events_f, prev_orders, prev_events)
    render_customer_profile(users, events_f)

    col1, col2 = st.columns(2)
    with col1:
        render_segment_ranking(orders_f)
    with col2:
        render_channel_ranking(users, orders_f)

    render_category_ranking(orders_f)

    col_trend, col_funnel = st.columns(2)
    with col_trend:
        render_gmv_chart(orders_f, granularity)
    with col_funnel:
        render_funnel(events_f)

    with st.expander("상세 분석 (RFM 산포도 · 코호트 리텐션)"):
        col4, col5 = st.columns(2)
        with col4:
            render_rfm_scatter(orders_f)
        with col5:
            render_cohort(users, orders_f)