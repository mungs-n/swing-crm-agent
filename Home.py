import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from datetime import timedelta

from components.charts import load_data

load_dotenv()

LOGO_PATH = "assets/ATHLEPA 로고.png"

SIDEBAR_CSS = """
<style>
[data-testid="stSidebar"] {
    min-width: 190px !important;
    max-width: 190px !important;
    width: 190px !important;
}
</style>
"""

st.set_page_config(
    page_title="ATHLEPA CRM",
    page_icon=LOGO_PATH,
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo(LOGO_PATH, size="large")
st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)


def render_home():
    # --- 상단 브랜드 헤더 ---
    col_logo, col_title = st.columns([1, 9])
    with col_logo:
        st.image(LOGO_PATH, width=56)
    with col_title:
        st.markdown(
            "<div style='font-size:1.7rem;font-weight:800;margin-top:0.4rem'>ATHLEPA CRM</div>"
            "<div style='color:#868E96;font-size:0.9rem;margin-top:-0.3rem'>애슬레저 브랜드 ATHLEPA 고객 대시보드</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")

    # --- 요약 지표 + 최근 활동 ---
    try:
        users, orders, events = load_data()

        latest_date = events["timestamp"].max()
        active_30d = events[events["timestamp"] >= latest_date - timedelta(days=30)]["user_id"].nunique()
        gmv_total = orders["total_amount"].sum()

        last_order = orders.groupby("user_id")["order_date"].max()
        dormant_count = int((last_order < latest_date - timedelta(days=60)).sum())

        try:
            history_df = pd.read_csv("data/campaign_history.csv")
        except FileNotFoundError:
            history_df = pd.DataFrame()

        st.markdown("##### 오늘의 요약")
        kcol1, kcol2, kcol3, kcol4 = st.columns(4)
        with kcol1:
            with st.container(border=True):
                st.markdown(
                    "<div style='color:#868E96;font-size:0.85rem'>활성 고객 (최근 30일)</div>"
                    f"<div style='font-size:1.6rem;font-weight:700'>{active_30d:,}명</div>",
                    unsafe_allow_html=True,
                )
        with kcol2:
            with st.container(border=True):
                st.markdown(
                    "<div style='color:#868E96;font-size:0.85rem'>누적 GMV</div>"
                    f"<div style='font-size:1.6rem;font-weight:700'>₩{gmv_total / 1_000_000:.1f}M</div>",
                    unsafe_allow_html=True,
                )
        with kcol3:
            with st.container(border=True):
                st.markdown(
                    "<div style='color:#E8590C;font-size:0.85rem'>⚠️ 휴면 고객 (60일+)</div>"
                    f"<div style='font-size:1.6rem;font-weight:700'>{dormant_count:,}명</div>",
                    unsafe_allow_html=True,
                )
        with kcol4:
            with st.container(border=True):
                st.markdown(
                    "<div style='color:#868E96;font-size:0.85rem'>캠페인 발송 이력</div>"
                    f"<div style='font-size:1.6rem;font-weight:700'>{len(history_df):,}건</div>",
                    unsafe_allow_html=True,
                )

        st.markdown("")
        left, right = st.columns([1.3, 1])

        with left:
            st.markdown("##### 최근 캠페인 발송 이력")
            if history_df.empty:
                st.info("아직 발송 이력이 없습니다. 자동화 탭에서 첫 캠페인을 만들어보세요.")
            else:
                st.dataframe(history_df.tail(5).iloc[::-1], width='stretch', hide_index=True)

        with right:
            st.markdown("##### AI 인사이트")
            insight = st.session_state.get("ai_insight_result")
            with st.container(border=True):
                if insight:
                    preview = insight[:180] + ("…" if len(insight) > 180 else "")
                    st.markdown(preview)
                else:
                    st.caption("아직 생성된 인사이트가 없습니다.")
                if st.button("AI 인사이트 보러 가기 →", width='stretch'):
                    st.switch_page("pages/2_AI_Insights.py")

    except FileNotFoundError:
        st.warning("데이터 파일을 찾을 수 없습니다. data/ 폴더를 확인해주세요.")

    st.markdown("---")

    # --- 바로가기 ---
    st.markdown("##### 바로가기")
    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("📊 대시보드", width='stretch'):
            st.switch_page("pages/1_Dashboard.py")
    with b2:
        if st.button("🔍 고객 분석", width='stretch'):
            st.switch_page("pages/2_AI_Insights.py")
    with b3:
        if st.button("🤖 자동화", width='stretch'):
            st.switch_page("pages/2_Campaign.py")


pg = st.navigation(
    [
        st.Page(render_home, title="홈", default=True),
        st.Page("pages/1_Dashboard.py", title="대시보드"),
        st.Page("pages/2_AI_Insights.py", title="고객 분석"),
        st.Page("pages/2_Campaign.py", title="자동화"),
    ]
)
pg.run()
