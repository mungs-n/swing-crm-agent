"""
담당자: 탭2 담당자 B
작업 내용: SendGrid 이메일 발송, 발송 이력 저장, 예약 발송 설정
"""

import streamlit as st
import sendgrid
from sendgrid.helpers.mail import Mail
import pandas as pd
import os
import json
from datetime import datetime, time as dtime


HISTORY_FILE = "data/campaign_history.csv"
TEST_RECIPIENTS_FILE = "data/test_recipients.csv"
SCHEDULE_FILE = "data/campaign_schedule.json"

WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]


def load_test_recipients():
    try:
        return pd.read_csv(TEST_RECIPIENTS_FILE)
    except FileNotFoundError:
        return pd.DataFrame(columns=["name", "email"])


def send_email(to_email, subject, body):
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
    df.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")


def load_schedule():
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_schedule(schedule):
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)


def render_schedule_settings(copy, segment, count):
    st.subheader("⏰ 예약 발송 설정")
    st.caption(
        "여기서 설정하고 저장한 뒤 **git commit & push까지 해야** "
        "GitHub Actions가 그 설정을 보고 자동 발송을 실행해요."
    )

    existing = load_schedule()

    with st.form("schedule_form"):
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "시작일",
                value=datetime.strptime(existing["start_date"], "%Y-%m-%d").date()
                if existing else datetime.now().date()
            )
            start_time = st.time_input(
                "발송 시작 시간",
                value=dtime.fromisoformat(existing["start_time"]) if existing else dtime(9, 0)
            )
        with col2:
            end_date = st.date_input(
                "종료일",
                value=datetime.strptime(existing["end_date"], "%Y-%m-%d").date()
                if existing else datetime.now().date()
            )
            end_time = st.time_input(
                "발송 종료 시간",
                value=dtime.fromisoformat(existing["end_time"]) if existing else dtime(20, 0)
            )

        selected_days = st.multiselect(
            "발송 요일",
            options=WEEKDAY_LABELS,
            default=existing["days"] if existing else WEEKDAY_LABELS[:5]
        )

        submitted = st.form_submit_button("💾 예약 저장", type="primary")

        if submitted:
            if not selected_days:
                st.error("발송 요일을 하나 이상 선택해주세요.")
            else:
                schedule = {
                    "segment": segment,
                    "copy": copy,
                    "count": count,
                    "start_date": start_date.strftime("%Y-%m-%d"),
                    "end_date": end_date.strftime("%Y-%m-%d"),
                    "start_time": start_time.strftime("%H:%M"),
                    "end_time": end_time.strftime("%H:%M"),
                    "days": selected_days,
                    "sent_dates": existing["sent_dates"] if existing else []
                }
                save_schedule(schedule)
                st.success(
                    "✅ 예약 설정 저장 완료! "
                    "터미널에서 git add/commit/push까지 해야 실제로 자동 발송이 작동해요."
                )

    if existing:
        st.caption(
            f"현재 저장된 예약: **{existing['days']}요일**, "
            f"**{existing['start_time']} ~ {existing['end_time']}**, "
            f"**{existing['start_date']} ~ {existing['end_date']}**"
        )


def render_email_sender():
    st.subheader("📤 발송")

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
            recipients = load_test_recipients()

            if recipients.empty:
                st.warning(
                    "data/test_recipients.csv 에 등록된 테스트 수신자가 없습니다. "
                    "name,email 컬럼으로 팀원 이메일을 등록해주세요."
                )
            else:
                lines = copy.split("\n")
                subject = lines[0].replace("제목: ", "").strip()
                body = "\n".join(lines[2:]).replace("본문: ", "").strip()

                success_count = 0
                fail_count = 0
                total = len(recipients)
                progress_bar = st.progress(0)

                for i, row in recipients.iterrows():
                    try:
                        status = send_email(row["email"], subject, body)
                        if status == 202:
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception:
                        fail_count += 1
                    progress_bar.progress((i + 1) / total)

                st.success(
                    f"✅ 전체 발송 완료: 테스트 수신자 {total}명 중 {success_count}명 성공"
                    f"{f', {fail_count}명 실패' if fail_count else ''} "
                    f"(실제 서비스 기준 대상 세그먼트: {count}명)"
                )
                save_history(
                    segment,
                    copy,
                    total,
                    f"전체 발송 완료 (테스트 {total}명 중 {success_count}명 성공)"
                )

    st.markdown("---")
    render_schedule_settings(copy, segment, count)

    st.subheader("📋 발송 이력")
    try:
        history = pd.read_csv(HISTORY_FILE)
        st.dataframe(history, use_container_width=True)
    except FileNotFoundError:
        st.info("아직 발송 이력이 없습니다.")
