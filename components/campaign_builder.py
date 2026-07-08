"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성
"""

import streamlit as st
import anthropic
import pandas as pd
import os


PERSONAS = {
    "이탈 위험 고객": {"count": 152, "desc": "45일 이상 미구매, 재활성화 필요"},
    "휴면 고객": {"count": 198, "desc": "90일 이상 미방문, 윈백 캠페인 대상"},
    "신규 탐색자": {"count": 105, "desc": "가입 30일 이내, 첫 구매 유도 필요"},
    "충동 구매자": {"count": 200, "desc": "고빈도 소액, 신상품·기획전 반응 높음"},
    "할인 구매자": {"count": 200, "desc": "세일 시즌에만 반응, 쿠폰 효과적"},
    "브랜드 충성 고객": {"count": 150, "desc": "중빈도 재구매, VIP 혜택 선호"},
}


def generate_email_copy(segment, persona_desc):
    """Claude API로 이메일 카피 생성"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system="""당신은 이커머스 CRM 마케팅 카피라이터입니다.
주어진 고객 세그먼트 특성에 맞는 개인화 이메일을 한국어로 작성하세요.
형식:
제목: (제목)
본문: (2-3문장)""",
        messages=[{
            "role": "user",
            "content": f"세그먼트: {segment}\n특성: {persona_desc}\n이 고객군에게 보낼 이메일을 작성해주세요."
        }]
    ) as stream:
        for text in stream.text_stream:
            yield text


def render_campaign_builder():
    """캠페인 빌더 UI - Campaigns.py에서 호출"""
    st.subheader("📋 캠페인 설정")

    col1, col2 = st.columns(2)

    with col1:
        selected_segment = st.selectbox(
            "타겟 세그먼트",
            options=list(PERSONAS.keys())
        )
        persona_info = PERSONAS[selected_segment]
        st.caption(f"대상 인원: {persona_info['count']}명 | {persona_info['desc']}")

    with col2:
        st.text_input("발송 채널", value="이메일 (SendGrid)", disabled=True)

    generate_btn = st.button("✨ AI 카피 자동 생성", type="primary")

    if generate_btn:
        st.subheader("생성된 메시지")
        with st.spinner("Claude가 카피를 작성하고 있습니다..."):
            full_response = ""
            placeholder = st.empty()
            for chunk in generate_email_copy(selected_segment, persona_info['desc']):
                full_response += chunk
                placeholder.markdown(full_response)

        # 세션 상태에 저장해서 email_sender.py에서 사용
        st.session_state["generated_copy"] = full_response
        st.session_state["selected_segment"] = selected_segment
        st.session_state["target_count"] = persona_info["count"]
