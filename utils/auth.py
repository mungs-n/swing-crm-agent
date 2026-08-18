"""
로그인 기반 멀티테넌시. platform_users/companies 테이블을 이용해 이메일+비밀번호로
로그인하면, 그 계정이 속한 회사의 dataset_source가 session_state에 저장된다.
이후 dashboard/charts.py 등은 이 값을 그대로 데이터 조회 조건으로 쓴다.
"""

import os

import bcrypt
import streamlit as st
from supabase import create_client


def _get_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    return bcrypt.checkpw(raw.encode(), hashed.encode())


def is_logged_in() -> bool:
    return st.session_state.get("auth_company_id") is not None


def login(email: str, password: str) -> bool:
    """성공하면 session_state에 회사 정보를 채우고 True 반환."""
    sb = _get_client()
    res = sb.table("platform_users").select("*, companies(*)").eq("email", email).limit(1).execute()
    if not res.data:
        return False
    user = res.data[0]
    if not verify_password(password, user["password_hash"]):
        return False

    company = user["companies"]
    st.session_state["auth_company_id"] = company["company_id"]
    st.session_state["auth_company_name"] = company["company_name"]
    st.session_state["dataset_source"] = company["dataset_source"]
    st.session_state["auth_email"] = email
    return True


def logout():
    for key in ["auth_company_id", "auth_company_name", "dataset_source", "auth_email"]:
        st.session_state.pop(key, None)


def render_login_form():
    """로그인 안 된 상태에서 보여줄 화면. 성공하면 st.rerun()으로 넘어간다."""
    st.markdown(
        "<div style='max-width:360px;margin:80px auto 0'>"
        "<h2 style='text-align:center'>ATHLEPA CRM</h2>"
        "<p style='text-align:center;color:var(--athlepa-muted-text);font-size:13px'>"
        "회사 계정으로 로그인하세요</p></div>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        with st.form("login_form"):
            email = st.text_input("이메일")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")
        if submitted:
            if login(email, password):
                st.rerun()
            else:
                st.error("이메일 또는 비밀번호가 올바르지 않습니다.")

        with st.expander("데모 계정 안내"):
            st.caption("athlepa@demo.com / demo1234 — ATHLEPA")
            st.caption("dacon@demo.com / demo1234 — 데이콘 리테일(예시 2번째 기업)")


def render_logout_button():
    with st.sidebar:
        st.markdown(
            f"<div style='font-size:12px;color:var(--athlepa-muted-text);margin-bottom:2px'>"
            f"{st.session_state.get('auth_company_name', '')}</div>",
            unsafe_allow_html=True,
        )
        if st.button("로그아웃", use_container_width=True, key="btn_logout"):
            logout()
            st.rerun()
