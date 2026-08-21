import os

import streamlit as st
from dotenv import load_dotenv

from utils.auth import is_logged_in, render_login_form, render_signup_form, render_logout_button, render_onboarding_screen
from utils.data_loader import DATASET_OPTIONS, get_dataset_source

load_dotenv()

# 로컬 개발 중엔 로그인 화면이 매번 번거로우므로, .env에 SKIP_LOGIN=true를 넣으면
# 로그인 없이 데이터셋 선택만으로 바로 대시보드에 들어갈 수 있다.
# 배포 환경(Render 등)에는 이 값을 넣지 않으므로 실제 데모/운영에서는 로그인이 그대로 유지된다.
SKIP_LOGIN = os.getenv("SKIP_LOGIN", "false").lower() == "true"

LOGO_PATH = "assets/ATHLEPA 로고.png"
THEME_CSS_PATH = "assets/theme.css"

st.set_page_config(
    page_title="ATHLEPA CRM",
    page_icon=LOGO_PATH,
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo(LOGO_PATH, size="large")

with open(THEME_CSS_PATH, "r", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def _run_app():
    from ab_test.page import render_ab_test
    from performance.page import render_performance

    pg = st.navigation({
        "": [
            st.Page("dashboard/page.py", title="대시보드", icon=":material/dashboard:", url_path="dashboard", default=True),
        ],
        "자동화": [
            st.Page("automation/page.py", title="캠페인 관리", icon=":material/campaign:", url_path="automation-campaign"),
            st.Page(render_ab_test, title="A/B 테스트", icon=":material/science:", url_path="automation-ab-test"),
            st.Page(render_performance, title="퍼포먼스 대시보드", icon=":material/insights:", url_path="automation-performance"),
        ],
    })
    pg.run()

    from ai_insights.chatbot import render_floating_chat
    render_floating_chat(force_open_once=st.session_state.pop("_force_open_ai_chat", False))


if SKIP_LOGIN:
    with st.sidebar:
        st.caption("🔓 로그인 생략 모드 (로컬 개발용)")
        dataset_source = st.selectbox(
            "데이터셋",
            options=list(DATASET_OPTIONS.keys()),
            format_func=lambda k: DATASET_OPTIONS[k],
            key="dev_dataset_select",
        )
    st.session_state["dataset_source"] = dataset_source
    st.session_state.setdefault("auth_company_name", DATASET_OPTIONS[dataset_source])
    _run_app()
elif not is_logged_in():
    if st.session_state.get("_auth_view") == "signup":
        render_signup_form()
    else:
        render_login_form()
elif st.session_state.get("_onboarding_keys"):
    render_onboarding_screen()
else:
    render_logout_button()
    _run_app()
