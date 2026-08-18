"""
로그인 기반 멀티테넌시. platform_users/companies 테이블을 이용해 이메일+비밀번호로
로그인하면, 그 계정이 속한 회사의 dataset_source가 session_state에 저장된다.
이후 dashboard/charts.py 등은 이 값을 그대로 데이터 조회 조건으로 쓴다.
"""

import os
import re
import secrets

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


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "company"


def signup(company_name: str, email: str, password: str) -> tuple[bool, str]:
    """새 회사를 회원가입시킨다. company_id/dataset_source를 회사명에서 새로 만들어서,
    기존 ATHLEPA/데이콘 데이터와 완전히 분리된 자기 데이터셋으로 시작한다.
    성공하면 로그인 상태로 만들고 트래킹용 api_key/webhook_secret을 세션에 남겨서
    가입 직후 한 번 보여줄 수 있게 한다."""
    sb = _get_client()

    existing = sb.table("platform_users").select("email").eq("email", email).limit(1).execute()
    if existing.data:
        return False, "이미 등록된 이메일입니다."

    base_slug = _slugify(company_name)
    company_id = f"{base_slug}-{secrets.token_hex(3)}"
    api_key = f"{base_slug}_pub_{secrets.token_hex(8)}"
    webhook_secret = f"{base_slug}_wh_{secrets.token_hex(12)}"

    sb.table("companies").insert(
        {
            "company_id": company_id,
            "company_name": company_name,
            "dataset_source": company_id,
            "api_key": api_key,
            "webhook_secret": webhook_secret,
        }
    ).execute()
    sb.table("platform_users").insert(
        {"email": email, "password_hash": hash_password(password), "company_id": company_id}
    ).execute()

    st.session_state["auth_company_id"] = company_id
    st.session_state["auth_company_name"] = company_name
    st.session_state["dataset_source"] = company_id
    st.session_state["auth_email"] = email
    st.session_state["_onboarding_keys"] = {"api_key": api_key, "webhook_secret": webhook_secret}
    return True, ""


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

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        if st.button("계정이 없으신가요? 회사 등록하기", use_container_width=True, type="tertiary", key="to_signup"):
            st.session_state["_auth_view"] = "signup"
            st.rerun()


def render_signup_form():
    """새 회사가 스스로 계정을 만드는 화면. 회사명만 있으면 그 회사만의
    dataset_source/api_key/webhook_secret이 자동으로 발급된다."""
    st.markdown(
        "<div style='max-width:360px;margin:80px auto 0'>"
        "<h2 style='text-align:center'>회사 계정 등록</h2>"
        "<p style='text-align:center;color:var(--athlepa-muted-text);font-size:13px'>"
        "새 회사로 ATHLEPA CRM을 시작하세요</p></div>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        with st.form("signup_form"):
            company_name = st.text_input("회사명")
            email = st.text_input("이메일")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("가입하기", use_container_width=True, type="primary")
        if submitted:
            if not company_name or not email or not password:
                st.error("모든 항목을 입력해주세요.")
            else:
                ok, msg = signup(company_name, email, password)
                if ok:
                    st.rerun()
                else:
                    st.error(msg)

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        if st.button("이미 계정이 있으신가요? 로그인", use_container_width=True, type="tertiary", key="to_login"):
            st.session_state["_auth_view"] = "login"
            st.rerun()


def render_onboarding_screen():
    """가입 직후 딱 한 번 보여주는 화면. 트래킹 스니펫/웹훅에 쓸 키를 안내한다."""
    keys = st.session_state.get("_onboarding_keys", {})
    st.markdown(
        "<div style='max-width:520px;margin:60px auto 0'>"
        "<h2 style='text-align:center'>가입 완료!</h2>"
        "<p style='text-align:center;color:var(--athlepa-muted-text);font-size:13px'>"
        "아래 키는 다시 보여드리지 않으니 지금 복사해두세요</p></div>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.markdown("**웹사이트에 심는 트래킹 키 (공개용)**")
        st.code(keys.get("api_key", ""), language=None)
        st.caption("고객 웹사이트 스니펫의 data-api-key 값으로 사용하세요.")

        st.markdown("**주문 데이터 연동용 웹훅 시크릿 (절대 노출 금지)**")
        st.code(keys.get("webhook_secret", ""), language=None)
        st.caption("자사 서버(백엔드)에서 주문 완료 시 이 값으로 인증해서 매출 데이터를 보내세요. 브라우저 코드에는 절대 넣지 마세요.")

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        if st.button("시작하기", use_container_width=True, type="primary"):
            st.session_state.pop("_onboarding_keys", None)
            st.rerun()


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
