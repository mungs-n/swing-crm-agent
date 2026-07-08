import streamlit as st
from components.campaign_builder import render_campaign_builder
from components.email_sender import render_email_sender

st.set_page_config(page_title="캠페인 자동화", page_icon="🤖", layout="wide")
st.title("🤖 AI 캠페인 자동화")
st.markdown("---")

# 캠페인 설정 및 카피 생성 (탭2 담당자 A)
render_campaign_builder()

st.markdown("---")

# 이메일 발송 및 이력 저장 (탭2 담당자 B)
render_email_sender()
