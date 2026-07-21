import streamlit as st

from components.campaign_builder import render_campaign_builder
from components.email_sender import render_email_sender

st.set_page_config(
    page_title="캠페인 자동화 - AI CRM",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🤖 CRM 마케팅 자동화")
render_campaign_builder()
st.markdown("---")
render_email_sender()
