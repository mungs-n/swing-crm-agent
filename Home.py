import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BRAND_PURPLE = "#9C8FCB"
LOGO_PATH = "assets/athlepa_logo.svg"

st.set_page_config(
    page_title="ATHLEPA CRM",
    page_icon="assets/athlepa_logo.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.logo(LOGO_PATH, size="large")

_, logo_col, _ = st.columns([1, 1, 1])
with logo_col:
    st.image(LOGO_PATH, width=90)

st.markdown(
    """
    <div style='text-align:center;padding:0 0 1rem 0;margin-top:-1rem'>
        <div style='font-size:2.2rem;font-weight:800;letter-spacing:0.02em'>ATHLEPA CRM</div>
        <div style='color:#868E96;font-size:1.05rem;margin-top:0.4rem'>
            AI 기반 고객 데이터 인텔리전스 플랫폼
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div style='max-width:640px;margin:0 auto 2.5rem auto;text-align:center;color:#495057;line-height:1.6'>
        ATHLEPA의 고객·매출 데이터를 한 곳에서 분석하고, AI가 데이터를 해석해 다음 액션을 제안하며,
        추천된 세그먼트로 바로 캠페인을 실행할 수 있도록 설계된 대시보드입니다.
        왼쪽 사이드바에서 원하는 화면으로 이동하세요.
    </div>
    """,
    unsafe_allow_html=True,
)

col1, col2, col3 = st.columns(3)

with col1:
    with st.container(border=True):
        st.markdown(
            f"""
            <div style='font-size:1.8rem'>📊</div>
            <div style='font-size:1.2rem;font-weight:700;margin-top:0.3rem'>대시보드</div>
            <div style='color:#868E96;font-size:0.92rem;margin-top:0.4rem;line-height:1.5'>
                KPI, 고객 프로필, 매출 랭킹, GMV 추이, 구매 퍼널 등
                핵심 지표를 한눈에 확인합니다.
            </div>
            """,
            unsafe_allow_html=True,
        )

with col2:
    with st.container(border=True):
        st.markdown(
            f"""
            <div style='font-size:1.8rem'>🤖</div>
            <div style='font-size:1.2rem;font-weight:700;margin-top:0.3rem'>AI 인사이트</div>
            <div style='color:#868E96;font-size:0.92rem;margin-top:0.4rem;line-height:1.5'>
                현재 데이터를 Claude가 분석해서 가장 시급한 문제와
                권장 액션을 리포트 형태로 제공합니다.
            </div>
            """,
            unsafe_allow_html=True,
        )

with col3:
    with st.container(border=True):
        st.markdown(
            f"""
            <div style='font-size:1.8rem'>📋</div>
            <div style='font-size:1.2rem;font-weight:700;margin-top:0.3rem'>캠페인 자동화</div>
            <div style='color:#868E96;font-size:0.92rem;margin-top:0.4rem;line-height:1.5'>
                AI가 추천한 세그먼트를 대상으로 맞춤 카피를 생성하고
                바로 이메일 발송까지 진행합니다.
            </div>
            """,
            unsafe_allow_html=True,
        )
