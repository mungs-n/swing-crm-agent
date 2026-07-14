"""
담당자: 탭1 AI 인사이트 담당
작업 내용: Claude API 연동, AI 인사이트 패널, Tool Use 구현
"""

import streamlit as st
import anthropic
import os
import pandas as pd
from datetime import timedelta


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

def run_ai_analysis(summary):
    """Claude API 호출해서 인사이트 생성"""
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


def render_ai_panel():
    """AI 인사이트 패널 - Dashboard.py에서 호출"""
    st.subheader("🤖 AI 인사이트")

    col1, col2 = st.columns([3, 1])
    with col2:
        analyze_btn = st.button("AI 분석 실행", type="primary", use_container_width=True)

    with col1:
        result_placeholder = st.empty()

    if analyze_btn:
        summary = get_dashboard_summary()
        with result_placeholder.container():
            with st.spinner("Claude가 데이터를 분석하고 있습니다..."):
                full_response = ""
                response_placeholder = st.empty()
                for chunk in run_ai_analysis(summary):
                    full_response += chunk
                    response_placeholder.markdown(full_response)
    else:
        result_placeholder.info("'AI 분석 실행' 버튼을 클릭하면 현재 데이터를 기반으로 인사이트를 생성합니다.")
