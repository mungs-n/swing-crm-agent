import streamlit as st

st.set_page_config(
    page_title="AI CRM Dashboard",
    page_icon="📊",
    layout="wide"
)

st.title("📊 AI CRM Dashboard")
st.markdown("---")

st.sidebar.title("사이드바")
menu = st.sidebar.radio(
    "이동할 페이지를 선택하세요.",
    ["대시보드", "캠페인 자동화"]
)

if menu == "🏠 홈 화면":
    st.title("🎯 CRM 마케팅 대시보드 홈")
    st.write("오신 것을 환영합니다! 왼쪽 사이드바 메뉴에서 원하는 기능을 선택해 보세요.")

# 2. 사용자가 '캠페인 빌더'를 선택했을 때 보여줄 화면
elif menu == "캠페인 자동화":
    # 기존에 만들었던 tester 파일에서 함수를 불러옵니다.
    # (파일 위치에 따라 components.campaign_builder_tester 또는 pages.1_Campaign_Builder 등으로 수정)
    try:
        from components.campaign_builder_tester import render_campaign_builder
        render_campaign_builder()
    except ImportError:
        from pages import render_campaign_builder
        render_campaign_builder()

st.markdown("""
### 안녕하세요! AI CRM 대시보드입니다.

왼쪽 사이드바에서 페이지를 선택하세요.

- 📊 **대시보드**: 고객 분석 및 AI 인사이트
- 🤖 **캠페인 자동화**: AI 기반 이메일 자동화
""") 
