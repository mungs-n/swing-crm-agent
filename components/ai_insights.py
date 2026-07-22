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


def get_dashboard_summary():
    """대시보드 현재 지표 요약 (Claude에게 넘길 컨텍스트) - 실제 CSV 기반 계산"""
    users = pd.read_csv("data/users.csv")
    orders = pd.read_csv("data/orders.csv")
    events = pd.read_csv("data/events.csv")

    orders["order_date"] = pd.to_datetime(orders["order_date"])

    # 기준일: 데이터 안 가장 최근 주문일
    latest_date = orders["order_date"].max()
    last_30d = latest_date - timedelta(days=30)
    last_60d = latest_date - timedelta(days=60)

    # 전체 고객 / 최근 30일 활성 고객
    total_users = users["user_id"].nunique()
    active_users_30d = orders[orders["order_date"] >= last_30d]["user_id"].nunique()

    # GMV / AOV
    gmv = orders["total_amount"].sum()
    order_count = len(orders)
    aov = gmv / order_count if order_count > 0 else 0

    # 구매 전환율: (구매 유저 수) / (전체 방문 유저 수)
    purchase_users = events[events["event_type"] == "purchase"]["user_id"].nunique()
    all_visitor_users = events["user_id"].nunique()
    conversion_rate = (purchase_users / all_visitor_users * 100) if all_visitor_users > 0 else 0

    # 장바구니 이탈률: 담기만 하고 구매 안 한 비율
    cart_users = events[events["event_type"] == "add_to_cart"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    # 휴면 고객: 마지막 주문이 60일 이상 지난 고객
    last_order_per_user = orders.groupby("user_id")["order_date"].max()
    dormant_users = (last_order_per_user < last_60d).sum()

    return f"""
    현재 대시보드 지표 (데이터 기준일: {latest_date.date()}):
    - 전체 가입 고객 수: {total_users}명
    - 최근 30일 활성 고객 수: {active_users_30d}명
    - GMV(총 거래액): ₩{gmv:,.0f}
    - AOV(평균 주문 금액): ₩{aov:,.0f}
    - 구매 전환율: {conversion_rate:.1f}%
    - 장바구니 이탈률: {cart_abandon_rate:.1f}%
    - 휴면 고객(60일 이상 미주문): {dormant_users}명
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
        max_tokens=500,
        system="""당신은 이커머스 CRM 분석 전문가입니다.
주어진 대시보드 데이터를 분석해서, 실무자가 대시보드 하단에서 바로 읽을 수 있는
짧고 임팩트 있는 인사이트를 한국어로 작성하세요.

규칙:
- 전체 2~3문장, 하나의 짧은 문단으로 작성
- 가장 시급한 문제 1가지를 중심으로 서술
- 핵심 수치나 세그먼트명은 [대괄호]로 강조 표시
- 표, 이모지, 번호 매기기 사용 금지
- 마지막 문장은 권장 액션 1가지로 마무리

예시 톤: "이달 가장 시급한 이슈는 [이탈 위험 고객] 증가입니다. 특히 [할인 선호] 세그먼트에서
집중 발생 중이며 마지막 구매로부터 평균 [47일] 경과했습니다. [리마인드 쿠폰 캠페인]이
즉시 필요한 상황입니다.\"""",
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
    """AI 인사이트 패널 - Dashboard.py에서 호출"""

    # 세션 상태 초기화 (최초 1회만 자동 분석, 이후엔 저장된 결과 재사용)
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
        """분석 실행 후 결과를 session_state에 저장"""
        summary = get_dashboard_summary()
        with result_placeholder.container():
            with st.spinner("Claude가 데이터를 분석하고 있습니다..."):
                full_response = ""
                response_placeholder = st.empty()
                for chunk in run_ai_analysis(summary):
                    full_response += chunk
                    response_placeholder.markdown(full_response)
        st.session_state.ai_insight_result = full_response
        st.session_state.ai_insight_time = datetime.now().strftime("%H:%M 분석")

    # 최초 진입: 자동으로 한 번 분석 실행
    if st.session_state.ai_insight_result is None:
        run_and_store()
        st.rerun()  # 배지를 표시하기 위해 한 번 새로고침
    else:
        # 저장된 결과 재사용 (API 재호출 없음)
        result_placeholder.markdown(st.session_state.ai_insight_result)

    # 재분석 버튼 + 캠페인 이동 버튼
    btn_col1, btn_col2 = st.columns([1, 3])
    with btn_col1:
        if st.button("🔄 다시 분석하기"):
            run_and_store()
            st.rerun()
    with btn_col2:
        if st.button("🚨 이탈 위험 캠페인 만들기", type="secondary"):
            st.switch_page("pages/2_Campaign.py")
