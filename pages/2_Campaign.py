import streamlit as st

from components.campaign_builder import render_campaign_builder
from components.email_sender import render_email_sender

st.title("🤖 자동화")
render_campaign_builder()
st.markdown("---")
render_email_sender()
