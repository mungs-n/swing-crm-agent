import streamlit as st

from ab_test.page import render_ab_test
from automation.campaign_builder import render_campaign_builder
from automation.email_sender import render_history
from performance.page import render_performance

st.title(":material/bolt: 캠페인 자동화")

tab_build, tab_ab, tab_perf = st.tabs(["캠페인 만들기", "A/B 테스트", "퍼포먼스 대시보드"])

with tab_build:
    render_campaign_builder()
    st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
    render_history()

with tab_ab:
    render_ab_test()

with tab_perf:
    render_performance()
