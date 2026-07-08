"""
담당자: 탭1 AI 인사이트 담당
작업 내용: Claude API 연동, AI 인사이트 패널, Tool Use 구현
"""

import streamlit as st
import anthropic
import os


def get_dashboard_summary():
    """대시보드 현재 지표 요약 (Claude에게 넘길 컨텍스트)"""
    # TODO: 실제 계산된 지표로 교체
    return """
    현재 대시보드 지표:
    - 활성 고객 수: 742명 (전월 대비 +3.2%)
    - GMV: ₩99.7M (+10.8%)
    - AOV: ₩37,900 (-0.1%)
    - 구매 전환율: 6.2% (+13.3%)
    - 30일 이탈률: 18.4% (+2.1%p 악화)
    - 이탈 위험 고객: 152명
    - 휴면 고객: 198명
    - 장바구니 이탈률: 93.9%
    """


def run_ai_analysis(summary):
    """Claude API 호출해서 인사이트 생성"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system="""당신은 이커머스 CRM 분석 전문가입니다.
주어진 대시보드 데이터를 분석해서 핵심 이슈와 권장 액션을 한국어로 작성하세요.
형식:
1. 가장 시급한 문제 1가지
2. 긍정적인 신호 1가지
3. 권장 액션 2가지""",
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
