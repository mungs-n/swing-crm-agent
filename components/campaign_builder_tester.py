"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성
"""

import streamlit as st
import anthropic
import pandas as pd
import os
import time


PERSONAS = {
    "신규 탐색자": {"count": 150, "desc": "가입 14일 이내, 첫 구매 유도 필요, 신규회원 전용 웰컴 혜택, 베스트셀러 추천"},
    "충동 구매자": {"count": 200, "desc": "고빈도 중액, 신상품·기획전 반응 높음, 시각적 매력, 희소성 있는 한정판, 매진 임박, 최신 트렌드, 직관성"},
    "할인 구매자": {"count": 200, "desc": "세일 시즌에만 반응, 할인 쿠폰이나 적립금 효과적, 할인율이나 할인 금액 명확히 보여주고 강조, 쿠폰 만료 임박 강조, 가성비, 긴박감"},
    "브랜드 충성 고객": {"count": 150, "desc": "중빈도 재구매, VIP 혜택 선호, 브랜드에 대한 신뢰도 높음, 신제품 사전 공개"},
    "이탈 위험 고객": {"count": 150, "desc": "45일 이상 미구매, 재활성화 필요, 호기심 자극하는 메일 제목, 장바구니 상품 리마인드, 파격적인 리텐션 쿠폰이나 혜택, 놓치기 아쉬운 혜택 강조"},
    "휴면 고객": {"count": 150, "desc": "90일 이상 미방문, 윈백 캠페인 대상, 다시 돌아오면 제공되는 혜택, 강렬한 한 줄 소구로 극적인 이메일 제목"},
}


def generate_email_copy_mock(segment, persona_desc):
    fake_response = f"제목: [테스트] {segment} 회원님을 위한 특별 혜택!\n\n본문: 안녕하세요. {persona_desc} 특성에 맞춘 마케팅 카피 테스트"
    for word in fake_response.split():
        yield word + " "
        time.sleep(0.1)


def render_campaign_builder():
    """캠페인 빌더 UI - Home.py에서 호출"""
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
            for chunk in generate_email_copy_mock(selected_segment, persona_info['desc']):
                full_response += chunk
                placeholder.markdown(full_response)

        # 세션 상태에 저장해서 email_sender.py에서 사용
        st.session_state["generated_copy"] = full_response
        st.session_state["selected_segment"] = selected_segment
        st.session_state["target_count"] = persona_info["count"]

render_campaign_builder()