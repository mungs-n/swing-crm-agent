"""
담당자: 탭2 담당자 B
작업 내용: SendGrid 이메일 발송, 발송 이력 저장
"""

import streamlit as st
import sendgrid
from sendgrid.helpers.mail import Mail
import pandas as pd
import os
from datetime import datetime


HISTORY_FILE = "data/campaign_history.csv"


def send_email(to_email, subject, body):
    """SendGrid로 실제 이메일 발송"""
    sg = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY"))
    message = Mail(
        from_email=os.environ.get("FROM_EMAIL"),
        to_emails=to_email,
        subject=subject,
        html_content=body.replace("\n", "<br>")
    )
    response = sg.send(message)
    return response.status_code


def save_history(segment, copy, count, status):
    """발송 이력 CSV에 저장"""
    new_row = {
        "발송일시": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "세그먼트": segment,
        "대상 인원": count,
        "메시지 요약": copy[:50] + "...",
        "상태": status
    }

    try:
        df = pd.read_csv(HISTORY_FILE)
    except FileNotFoundError:
        df = pd.DataFrame(columns=new_row.keys())

    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    # utf-8-sig로 저장해야 엑셀에서 열었을 때 한글이 깨지지 않음
    df.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")


def render_email_sender():
    """이메일 발송 UI - Campaigns.py에서 호출"""
    st.subheader("📤 발송")

    # campaign_builder.py에서 생성한 카피 가져오기
    if "generated_copy" not in st.session_state:
        st.info("위에서 AI 카피를 먼저 생성해주세요.")
        return

    copy = st.session_state["generated_copy"]
    segment = st.session_state["selected_segment"]
    count = st.session_state["target_count"]

    test_email = st.text_input(
        "테스트 발송 이메일",
        placeholder="본인 이메일 주소 입력"
    )

    col1, col2 = st.columns(2)

    with col1:
        if st.button("📧 테스트 발송", use_container_width=True):
            if test_email:
                try:
                    lines = copy.split("\n")
                    subject = lines[0].replace("제목: ", "").strip()
                    body = "\n".join(lines[2:]).replace("본문: ", "").strip()
                    status = send_email(test_email, subject, body)
                    if status == 202:
                        st.success(f"✅ 테스트 이메일 발송 완료 → {test_email}")
                        save_history(segment, copy, 1, "테스트 발송")
                except Exception as e:
                    st.error(f"발송 실패: {e}")
            else:
                st.warning("이메일 주소를 입력해주세요.")

    with col2:
        if st.button(f"🚀 전체 발송 ({count}명)", use_container_width=True, type="primary"):
            # 실제 발송은 데이터에서 이메일 목록 가져와서 반복
            st.warning("전체 발송 기능은 실제 이메일 목록 연동 후 활성화됩니다.")

    # 발송 이력
    st.subheader("📋 발송 이력")
    try:
        history = pd.read_csv(HISTORY_FILE)
        st.dataframe(history, use_container_width=True)
    except FileNotFoundError:
        st.info("아직 발송 이력이 없습니다.")
