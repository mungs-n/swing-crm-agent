"""
A/B 테스트 서브탭 진입점
담당: 가연

automation/page.py에서 서브탭(캠페인 만들기 / A/B 테스트 / 퍼포먼스 대시보드)을 만들 때
'A/B 테스트' 서브탭에서 render_ab_test()를 호출하면 됩니다.

예)
    tab_build, tab_ab, tab_perf = st.tabs(["캠페인 만들기", "A/B 테스트", "퍼포먼스 대시보드"])
    with tab_ab:
        from ab_test.page import render_ab_test
        render_ab_test()
"""

import streamlit as st

from ab_test.wizard import render_wizard
from ab_test.results import render_results, render_summary_header


def render_ab_test():
    if st.session_state.get("ab_wizard_open", False):
        _render_wizard_view()
    else:
        _render_list_view()


def _render_list_view():
    hcol1, hcol2 = st.columns([5, 1.4])
    with hcol1:
        render_summary_header()
    with hcol2:
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        if st.button("+ 새 A/B 테스트", type="primary", use_container_width=True, key="ab-new-test-btn"):
            st.session_state["ab_wizard_open"] = True
            st.rerun()

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    render_results()


def _render_wizard_view():
    """목록 화면을 완전히 대체하는 A/B 테스트 생성 화면 (아코디언 없이 전체 화면 전환)."""
    if st.button(":material/arrow_back: 목록으로  /  새 A/B 테스트", key="ab-wizard-back", type="tertiary"):
        st.session_state["ab_wizard_open"] = False
        st.rerun()
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    render_wizard()
