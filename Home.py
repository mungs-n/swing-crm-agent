import streamlit as st

st.set_page_config(
    page_title="AI CRM Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 AI CRM Dashboard")
st.markdown("---")

st.markdown("""
### 안녕하세요! AI CRM 대시보드입니다.

왼쪽 사이드바에서 페이지를 선택하세요.

- 📊 **대시보드**: 고객 분석 및 AI 인사이트
- 🤖 **캠페인 자동화**: AI 기반 이메일 자동화
""") 
