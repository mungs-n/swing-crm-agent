import streamlit as st

from components.ai_insights import render_ai_panel

st.set_page_config(
    page_title="AI 인사이트 - AI CRM",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("AI 인사이트")
render_ai_panel()
