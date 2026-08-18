import streamlit as st

from ai_insights.chatbot import render_ai_panel, render_status_badge

col_title, col_status = st.columns([3, 1])
with col_title:
    st.title("고객 분석")
with col_status:
    render_status_badge()

render_ai_panel()
