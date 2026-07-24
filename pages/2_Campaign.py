import streamlit as st

from components.campaign_builder import render_campaign_builder
from components.email_sender import render_email_sender

st.set_page_config(
    page_title="캠페인 자동화 - ATHLEPA CRM",
    page_icon="assets/athlepa_logo.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo("assets/athlepa_logo.svg", size="large")

st.title("🤖 CRM 마케팅 자동화")
render_campaign_builder()
st.markdown("---")
render_email_sender()
