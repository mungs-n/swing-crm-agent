"""
담당자: 탭1 AI 인사이트 담당
작업 내용: Claude API 연동, AI 인사이트 패널, RFM/코호트 상세 분석 통합
"""

import streamlit as st
import plotly.graph_objects as go
import anthropic
import os
import pandas as pd
from datetime import timedelta, datetime


# ---------------------------------------------------------
# 데이터 로드 & 요약
# ---------------------------------------------------------

@st.cache_data
def load_data():
    """CSV 로드 (캐싱됨). data/*.csv 파일을 직접 교체했다면
    Streamlit 메뉴의 'Clear cache' 또는 앱 재시작으로 캐시를 갱신해야 한다.

    encoding='utf-8-sig': 데이터 생성 스크립트가 엑셀 호환을 위해 BOM을
    포함해서 저장하므로, 명시하지 않으면 pandas/환경에 따라 첫 컬럼명에
    BOM 문자가 섞여 들어갈 수 있다."""
    users = pd.read_csv("data/users.csv", encoding="utf-8-sig")
    orders = pd.read_csv("data/orders.csv", encoding="utf-8-sig")
    events = pd.read_csv("data/events.csv", encoding="utf-8-sig")
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    return users, orders, events


def compute_recency_days(users: pd.DataFrame, orders: pd.DataFrame) -> pd.Series:
    """전체 유저(users.csv 기준) 대상으로 '마지막 구매로부터 경과일'을 계산한다.

    한 번도 구매하지 않은 유저는 orders 테이블에 나타나지 않으므로 빠뜨리기
    쉽다. 이 함수는 구매 이력이 있으면 마지막 구매일을, 없으면 가입일을
    기준으로 삼아 users.csv의 모든 유저에 대해 값을 계산한다 (즉 한 번도
    구매하지 않은 유저는 '가입 이후 경과일'을 휴면 기간으로 취급한다).
    """
    users = users.copy()
    orders = orders.copy()
    users["signup_date"] = pd.to_datetime(users["signup_date"])
    orders["order_date"] = pd.to_datetime(orders["order_date"])

    latest_date = orders["order_date"].max()
    last_order_date = orders.groupby("user_id")["order_date"].max()
    signup_date = users.set_index("user_id")["signup_date"]

    reference_date = last_order_date.reindex(signup_date.index)
    reference_date = reference_date.fillna(signup_date)

    return (latest_date - reference_date).dt.days


def compute_repeat_purchase_rate(orders: pd.DataFrame) -> float:
    """2회 이상 구매한 고객 비율(%). (구매 이력이 있는 고객 중 기준)"""
    order_counts = orders.groupby("user_id").size()
    if order_counts.shape[0] == 0:
        return 0.0
    return (order_counts >= 2).sum() / order_counts.shape[0] * 100


def compute_rfm(orders: pd.DataFrame) -> pd.DataFrame:
    """유저별 RFM(Recency, Frequency, Monetary) 계산"""
    latest_date = orders["order_date"].max()

    rfm = orders.groupby("user_id").agg(
        frequency=("order_id", "count"),
        monetary=("total_amount", "sum"),
        last_order=("order_date", "max"),
    )
    rfm["recency"] = (latest_date - rfm["last_order"]).dt.days
    return rfm.reset_index()


def compute_cohort_retention(users: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """가입월 기준 코호트 리텐션(%) 피벗 테이블 계산

    코호트 = 회원가입 월. 각 코호트 전체 인원 대비, 가입 후 N개월차에
    '구매'가 있었던 회원 비율(%)을 계산한다. (첫 구매월 기준이 아님 —
    가입만 하고 구매하지 않는 회원이 다수이므로 0개월차도 100%가 아니다.)

    주의: 데이터에 '관측 기간 시작 이전에 가입한 기존 고객'이 다수 포함돼
    있으므로(현재 데이터 생성 로직 기준 약 75%), 이런 고객은 코호트 자체가
    관측 기간보다 훨씬 이전이라 의미 있는 리텐션 곡선을 그릴 수 없다.
    (예: 2024년 1월 가입자의 '1개월차'는 2024년 2월인데, 주문 데이터 자체가
    2026년 1월부터 시작되어 그 시점엔 관측이 불가능하다.)
    따라서 orders 데이터의 관측 시작월 이후에 가입한 코호트만 포함한다.
    """
    users = users.copy()
    orders = orders.copy()

    users["signup_date"] = pd.to_datetime(users["signup_date"])
    users["cohort_month"] = users["signup_date"].dt.to_period("M")

    obs_start_month = orders["order_date"].min().to_period("M")
    users = users[users["cohort_month"] >= obs_start_month]

    merged = orders.merge(users[["user_id", "cohort_month"]], on="user_id", how="inner")
    merged["order_month"] = merged["order_date"].dt.to_period("M")
    merged["months_since"] = (
        merged["order_month"] - merged["cohort_month"]
    ).apply(lambda x: x.n)
    merged = merged[merged["months_since"] >= 0]

    cohort_sizes = users.groupby("cohort_month")["user_id"].nunique()
    active_counts = (
        merged.groupby(["cohort_month", "months_since"])["user_id"]
        .nunique()
        .reset_index()
    )
    pivot = active_counts.pivot(
        index="cohort_month", columns="months_since", values="user_id"
    )
    retention = pivot.divide(cohort_sizes, axis=0) * 100
    return retention.sort_index()


def get_dashboard_summary():
    """대시보드 현재 지표 요약 (Claude에게 넘길 컨텍스트) - 실제 CSV 기반 계산"""
    users, orders, events = load_data()

    latest_date = orders["order_date"].max()
    last_30d = latest_date - timedelta(days=30)

    total_users = users["user_id"].nunique()
    active_users_30d = orders[orders["order_date"] >= last_30d]["user_id"].nunique()

    gmv = orders["total_amount"].sum()
    order_count = len(orders)
    aov = gmv / order_count if order_count > 0 else 0

    purchase_users = events[events["event_type"] == "purchase"]["user_id"].nunique()
    all_visitor_users = events["user_id"].nunique()
    conversion_rate = (purchase_users / all_visitor_users * 100) if all_visitor_users > 0 else 0

    cart_users = events[events["event_type"] == "add_to_cart"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    # 휴면 고객: 90일 이상 미구매 (탭3 PERSONAS의 '휴면 고객' 정의와 통일).
    # 한 번도 구매하지 않은 유저도 포함해서 계산한다 (compute_recency_days 참고).
    recency_days = compute_recency_days(users, orders)
    dormant_users = (recency_days >= 90).sum()

    # --- 추가 지표: 원인 분석용 근거 데이터 ---
    repeat_purchase_rate = compute_repeat_purchase_rate(orders)

    user_revenue = orders.groupby("user_id")["total_amount"].sum().sort_values(ascending=False)
    top10_n = max(1, int(len(user_revenue) * 0.1))
    top10_revenue_share = (
        user_revenue.iloc[:top10_n].sum() / user_revenue.sum() * 100
        if user_revenue.sum() > 0 else 0
    )

    retention = compute_cohort_retention(users, orders)
    avg_month1_retention = retention[1].mean() if 1 in retention.columns else 0

    return f"""
    현재 대시보드 지표 (데이터 기준일: {latest_date.date()}):
    - 전체 가입 고객 수: {total_users}명
    - 최근 30일 활성 고객 수: {active_users_30d}명
    - GMV(총 거래액): ₩{gmv:,.0f}
    - AOV(평균 주문 금액): ₩{aov:,.0f}
    - 구매 전환율: {conversion_rate:.1f}%
    - 장바구니 이탈률: {cart_abandon_rate:.1f}%
    - 휴면 고객(90일 이상 미구매, 무구매 고객 포함): {dormant_users}명
    - 재구매율(2회 이상 구매 고객 비율): {repeat_purchase_rate:.1f}%
    - 상위 10% 고객의 매출 기여도: {top10_revenue_share:.1f}%
    - 신규 고객의 1개월차 평균 재방문율: {avg_month1_retention:.1f}%
    """


def recommend_segment(users: pd.DataFrame, orders: pd.DataFrame, events: pd.DataFrame) -> str:
    """실제 지표값을 기준으로 탭3 PERSONAS 키 중 하나를 규칙 기반으로 추천한다.

    주의: AI가 생성한 자유 텍스트를 파싱하지 않는다 — Claude의 응답 문구는
    매번 표현이 달라질 수 있어 세그먼트명이 PERSONAS 키와 정확히 일치한다는
    보장이 없기 때문이다. 대신 이미 계산된 지표(미구매 경과일, 신규 가입 여부,
    쿠폰 사용률 등)를 기준으로 파이썬이 직접 6개 키 중 하나를 결정한다.

    아래 임계값(45일/90일/30일 등)은 PERSONAS 딕셔너리의 설명 문구를 그대로
    가져온 값이다. 실제 팀 기준이 따로 있다면 그 값으로 조정하면 된다.
    """
    orders = orders.copy()
    users = users.copy()
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    users["signup_date"] = pd.to_datetime(users["signup_date"])

    latest_date = orders["order_date"].max()
    total_users = users["user_id"].nunique()

    # 유저별 경과일 (한 번도 구매하지 않은 유저도 포함 — compute_recency_days 참고)
    recency_days = compute_recency_days(users, orders)
    dormant_count = (recency_days >= 90).sum()                              # 휴면 고객 (90일+)
    at_risk_count = ((recency_days >= 45) & (recency_days < 90)).sum()      # 이탈 위험 (45~90일)

    # 신규 가입자(30일 이내) 중 구매 전환율
    new_users = users[(latest_date - users["signup_date"]).dt.days <= 30]
    new_user_ids = set(new_users["user_id"])
    new_user_purchasers = orders[orders["user_id"].isin(new_user_ids)]["user_id"].nunique()
    new_user_purchase_rate = (
        new_user_purchasers / len(new_users) if len(new_users) > 0 else 1.0
    )

    # 장바구니 이탈률
    cart_users = events[events["event_type"] == "add_to_cart"]["user_id"].nunique()
    purchase_users = events[events["event_type"] == "purchase"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    # 쿠폰 사용률 (할인 민감도)
    coupon_rate = orders["coupon_used"].mean() * 100 if "coupon_used" in orders.columns else 0

    # 재구매율 (충성도)
    repeat_purchase_rate = compute_repeat_purchase_rate(orders)

    # --- 우선순위 규칙: 가장 심각/시급한 문제부터 확인 ---
    if dormant_count >= total_users * 0.15:
        return "휴면 고객"
    if at_risk_count >= total_users * 0.10:
        return "이탈 위험 고객"
    if len(new_users) > 0 and new_user_purchase_rate < 0.3:
        return "신규 탐색자"
    if cart_abandon_rate >= 45:
        return "이탈 위험 고객"
    if coupon_rate >= 50:
        return "할인 구매자"
    if repeat_purchase_rate >= 70:
        return "브랜드 충성 고객"
    return "충동 구매자"


# ---------------------------------------------------------
# Claude API 호출
# ---------------------------------------------------------

def run_ai_analysis(summary):
    """Claude API 호출해서 인사이트 생성 (컨설팅 리포트 형식)"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2200,
        system="""당신은 이커머스 CRM 데이터 분석 전문가입니다.
대시보드 실무자가 의사결정에 바로 활용할 수 있는 전문적인 분석 리포트를
한국어 존댓말(합니다체)로 작성하세요.

다음 4개 섹션 구조로 작성하세요 (총 7~10문장 분량):

### 🎯 핵심 진단
가장 시급한 문제 1가지와 그 이유를 1~2문장으로 명확히 제시

### 🔍 원인 분석
관련 지표들을 근거로 왜 이런 현상이 발생했는지 2~3문장으로 설명
(이탈률, 재구매율, 코호트 재방문율 등 지표 간의 연결고리를 짚되,
서로 다른 고객군 기준의 지표를 인과관계처럼 단정하지 말고
"~로 추정됩니다", "~일 가능성이 있습니다"처럼 상관관계 수준으로 서술할 것)

### 📊 정량적 임팩트
방치 시 예상되는 손실, 혹은 개선 시 기대 효과를 구체적 수치로 제시 (2문장)

### ✅ 실행 권장사항
가장 우선순위 높은 액션 1~2가지만 간결하게 제시. 이 리포트를 읽고 바로
"캠페인 자동화" 버튼을 눌러 해당 세그먼트에 이메일을 발송하는 흐름으로
이어지므로, 여러 단계로 나뉜 세부 일정(예: 7일/14일/30일 단계별 플랜)보다는
"이 세그먼트에 어떤 메시지/혜택을 보낼지" 수준의 간단하고 실행 가능한
문장으로 작성

규칙:
- 반드시 위 4개 섹션을 ### 소제목(이모지 포함)으로 구분해서 작성
- 문장 끝은 반드시 존댓말로 마무리 ("~합니다", "~권장드립니다", "~필요합니다" 등).
  "~하라", "~할 것", "~이다" 같은 반말/명령형 어미는 절대 사용하지 말 것
- 핵심 수치나 세그먼트명은 [대괄호]로 강조
- 주어진 데이터에 근거해서만 서술하고, 없는 정보는 추측하지 말 것
- 서로 다른 고객 세그먼트(예: 전체 고객 vs 신규 고객)의 지표를 섞어서
  단정적 인과관계로 서술하지 말 것 — 상관관계 수준의 신중한 표현을 사용할 것
- 어떤 수치를 "높다/낮다/충분하다/부족하다"고 평가할 때는, 비교할 명확한
  기준(예: 다른 지표와의 상대적 차이, 시계열 변화, 업계 통념 등)을 함께 제시할
  수 있는 경우에만 그렇게 서술할 것. 비교 기준을 댈 수 없는 수치는 그 자체
  사실만 전달하고 좋다/나쁘다 판단은 하지 말 것
- 주어진 지표를 전부 다 언급할 필요는 없음. 스토리에 억지로 끼워 맞추지 말고,
  실제로 논리적 근거가 뚜렷한 지표 3~4개만 선택해서 사용할 것
- 본문 안에는 이모지·표를 넣지 말 것 (소제목에만 사용). 실무 보고서 톤 유지""",
        messages=[
            {"role": "user", "content": f"다음 데이터를 분석해주세요:\n{summary}"}
        ]
    ) as stream:
        for text in stream.text_stream:
            yield text

        # 스트림 종료 후, 토큰 한도 때문에 응답이 잘렸는지 확인
        final_message = stream.get_final_message()
        if final_message.stop_reason == "max_tokens":
            yield "\n\n> ⚠️ 응답 길이 제한으로 내용이 일부 잘렸습니다. '다시 분석하기'를 눌러주세요."


# ---------------------------------------------------------
# 상세 분석: RFM 산포도 + 코호트 리텐션 히트맵
# ---------------------------------------------------------

def render_rfm_scatter(rfm: pd.DataFrame):
    fig = go.Figure(
        data=go.Scatter(
            x=rfm["frequency"],
            y=rfm["monetary"],
            mode="markers",
            marker=dict(
                size=rfm["monetary"],
                sizemode="area",
                sizeref=2.0 * rfm["monetary"].max() / (40.0 ** 2),
                sizemin=4,
                color=rfm["recency"],
                colorscale="RdYlBu",
                colorbar=dict(title="최근성(일)"),
                line=dict(width=0.5, color="rgba(0,0,0,0.2)"),
                opacity=0.85,
            ),
            hovertemplate=(
                "구매 빈도: %{x}<br>"
                "누적 구매액: ₩%{y:,.0f}<br>"
                "최근성: %{marker.color}일<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        xaxis_title="구매 빈도",
        yaxis_title="누적 구매액",
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)", zeroline=False)
    st.plotly_chart(fig, use_container_width=True)


def render_cohort_heatmap(retention: pd.DataFrame):
    x_labels = [f"{c}개월차" for c in retention.columns]
    y_labels = [str(p) for p in retention.index]

    text = retention.round(1).astype(str) + "%"
    text = text.where(retention.notna(), "")

    fig = go.Figure(
        data=go.Heatmap(
            z=retention.values,
            x=x_labels,
            y=y_labels,
            text=text.values,
            texttemplate="%{text}",
            textfont=dict(size=12, color="white"),
            colorscale="YlOrRd",
            hoverongaps=False,
            colorbar=dict(title=""),
            xgap=3,
            ygap=3,
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_detail_analysis():
    """RFM 산포도 + 코호트 리텐션 히트맵을 나란히 표시"""
    users, orders, events = load_data()
    rfm = compute_rfm(orders)
    retention = compute_cohort_retention(users, orders)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**RFM 산포도**")
        render_rfm_scatter(rfm)
    with col2:
        st.markdown("**코호트 리텐션 히트맵**")
        render_cohort_heatmap(retention)


# ---------------------------------------------------------
# 메인 패널 (AI 인사이트 + 상세 분석 통합)
# ---------------------------------------------------------

def render_ai_panel():
    """AI 인사이트 + 상세 분석 패널 - Dashboard.py에서 호출"""

    if "ai_insight_result" not in st.session_state:
        st.session_state.ai_insight_result = None
        st.session_state.ai_insight_time = None

    header_col1, header_col2 = st.columns([3, 2])
    with header_col1:
        st.subheader("🤖 AI 인사이트")
    with header_col2:
        if st.session_state.ai_insight_result:
            st.markdown(
                f"""
                <div style="text-align: right; margin-top: 22px;">
                    <span style="
                        display: inline-block;
                        white-space: nowrap;
                        background-color: #EEF2FF;
                        color: #4F46E5;
                        padding: 4px 14px;
                        border-radius: 999px;
                        font-size: 13px;
                        font-weight: 500;
                    ">✨ Claude AI · {st.session_state.ai_insight_time}</span>
                </div>
                """,
                unsafe_allow_html=True
            )

    result_placeholder = st.empty()

    def run_and_store():
        summary = get_dashboard_summary()
        with result_placeholder.container():
            with st.spinner("Claude가 데이터를 분석하고 있습니다..."):
                full_response = ""
                response_placeholder = st.empty()
                try:
                    for chunk in run_ai_analysis(summary):
                        full_response += chunk
                        response_placeholder.markdown(full_response)
                except Exception as e:
                    if full_response:
                        response_placeholder.markdown(full_response)
                    st.warning(
                        f"분석 중 연결이 끊겼습니다 ({e}). "
                        "'다시 분석하기'를 눌러 재시도해주세요."
                    )
        st.session_state.ai_insight_result = full_response
        st.session_state.ai_insight_time = datetime.now().strftime("%H:%M 분석")

    if st.session_state.ai_insight_result is None:
        run_and_store()
        st.rerun()
    else:
        result_placeholder.markdown(st.session_state.ai_insight_result)

    btn_col1, btn_col2 = st.columns([1, 3])
    with btn_col1:
        if st.button("🔄 다시 분석하기"):
            run_and_store()
            st.rerun()
    with btn_col2:
        if st.button("🚀 캠페인 자동화 시작하기", type="secondary"):
            users, orders, events = load_data()
            st.session_state["recommended_segment"] = recommend_segment(users, orders, events)
            st.switch_page("pages/2_Campaigns.py")

    with st.expander("📊 상세 분석 (RFM 산포도 · 코호트 리텐션)", expanded=False):
        render_detail_analysis()