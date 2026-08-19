import streamlit as st

from ai_insights.chatbot import render_floating_chat, render_status_badge

col_title, col_status = st.columns([3, 1])
with col_title:
    st.title("고객 분석")
with col_status:
    render_status_badge()

st.info(
    "AI 어시스턴트는 화면 우측 하단의 플로팅 버튼으로 언제든 열 수 있어요. "
    "이 페이지에 들어오면 자동으로 한 번 열어드려요.",
    icon=":material/smart_toy:",
)

render_floating_chat(force_open_once=True)
