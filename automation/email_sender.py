"""
담당자: 탭2 담당자 B
작업 내용: SendGrid 이메일 발송, 발송 이력 저장, 예약 발송 설정
"""

import streamlit as st
import sendgrid
from sendgrid.helpers.mail import CustomArg, Mail
import pandas as pd
import os
import uuid
from datetime import datetime
from supabase import create_client

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except ImportError:
    KST = None


HISTORY_FILE = "data/campaign_history.csv"
TEST_RECIPIENTS_FILE = "data/test_recipients.csv"
SCHEDULED_FILE = "data/scheduled_emails.csv"


def load_test_recipients():
    try:
        return pd.read_csv(TEST_RECIPIENTS_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return pd.DataFrame(columns=["name", "email"])
    except UnicodeDecodeError:
        # 엑셀에서 저장하면 한글 Windows 기본 인코딩(CP949)으로 저장되는 경우가 있음
        return pd.read_csv(TEST_RECIPIENTS_FILE, encoding="cp949")


def send_email(to_email, subject, body, send_at: int | None = None, send_id: str | None = None):
    """SendGrid로 이메일을 보낸다. send_at(미래 시각의 unix timestamp)을 주면 지금 API를
    호출은 하지만, 실제 발송은 SendGrid가 그 시각에 알아서 처리한다 (예약 발송,
    SendGrid 자체 제약으로 최대 72시간 이내만 가능).

    send_id를 주면 custom_args로 실어 보내서, SendGrid Event Webhook이 오픈/클릭 이벤트에
    이 값을 그대로 실어 돌려준다 (ingestion_server의 /sendgrid-events가 이 값으로
    campaign_sends 테이블의 어느 행을 업데이트할지 찾는다). campaign_sends에 해당
    send_id로 행을 미리 만들어두지 않으면 업데이트 대상이 없어 이벤트가 무시된다."""
    sg = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY"))
    message = Mail(
        from_email=os.environ.get("FROM_EMAIL"),
        to_emails=to_email,
        subject=subject,
        html_content=body.replace("\n", "<br>")
    )
    if send_at is not None:
        message.send_at = send_at
    if send_id is not None:
        message.add_custom_arg(CustomArg("send_id", send_id))
    response = sg.send(message)
    return response.status_code


def _get_supabase_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def save_history(segment, copy, count, status, approval_mode="자동실행", campaign_id: str | None = None) -> str:
    """발송 이력을 저장한다. 기존처럼 로컬 CSV(data/campaign_history.csv)에 남기고,
    A/B 테스트·퍼포먼스 대시보드가 실시간으로 보는 Supabase campaign_history 테이블에도
    같이 남긴다 (안 그러면 실제 발송이 대시보드에 전혀 안 잡힘).

    campaign_id를 안 주면 여기서 새로 만든다 - 호출부에서 campaign_sends 행을 같이
    남기려면 campaign_id를 미리 만들어서 넘기고, 돌려받은 값을 그대로 쓰면 된다."""
    campaign_id = campaign_id or str(uuid.uuid4())[:8]
    message_summary = copy[:50] + "..."
    sent_at = datetime.now()

    new_row = {
        "campaign_id": campaign_id,
        "발송일시": sent_at.strftime("%Y-%m-%d %H:%M"),
        "세그먼트": segment,
        "대상 인원": count,
        "메시지 요약": message_summary,
        "상태": status,
        "approval_mode": approval_mode,
    }
    try:
        df = pd.read_csv(HISTORY_FILE)
    except FileNotFoundError:
        df = pd.DataFrame(columns=new_row.keys())
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    df.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")

    try:
        _get_supabase_client().table("campaign_history").upsert({
            "campaign_id": campaign_id,
            "sent_at": sent_at.isoformat(),
            "segment": segment,
            "target_count": count,
            "message_summary": message_summary,
            "status": status,
            "approval_mode": approval_mode,
        }, on_conflict="campaign_id").execute()
    except Exception as e:
        print(f"[save_history] Supabase campaign_history 저장 실패: {e}")

    return campaign_id


def record_campaign_send(campaign_id: str, user_id: str, segment: str, channel: str, send_id: str) -> None:
    """실제 발송 1건을 Supabase campaign_sends에 기록한다. SendGrid Event Webhook
    (ingestion_server의 /sendgrid-events)이 이 send_id로 오픈/클릭 이벤트를 매칭해서
    opened_at/clicked_at을 채워준다. 이 행을 먼저 만들어두지 않으면 웹훅 이벤트가
    매칭할 대상이 없어 무시된다."""
    try:
        _get_supabase_client().table("campaign_sends").upsert({
            "send_id": send_id,
            "campaign_id": campaign_id,
            "user_id": user_id,
            "segment": segment,
            "channel": channel,
            "sent_at": datetime.now().isoformat(),
            "delivered": True,
        }, on_conflict="send_id").execute()
    except Exception as e:
        print(f"[record_campaign_send] Supabase campaign_sends 저장 실패: {e}")


def save_scheduled_emails(segment, subject, recipients_emails, send_at_dt):
    new_rows = pd.DataFrame([{
        "등록일시": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "발송예정일시": send_at_dt.strftime("%Y-%m-%d %H:%M"),
        "받는사람": email,
        "세그먼트": segment,
        "제목": subject,
    } for email in recipients_emails])
    try:
        df = pd.read_csv(SCHEDULED_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        df = pd.DataFrame(columns=new_rows.columns)
    df = pd.concat([df, new_rows], ignore_index=True)
    df.to_csv(SCHEDULED_FILE, index=False, encoding="utf-8-sig")


def render_history():
    """발송 이력 - 예약된 목록 / 발송 완료 두 탭으로 나눠서 보여준다."""
    try:
        scheduled_df = pd.read_csv(SCHEDULED_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        scheduled_df = pd.DataFrame(columns=["등록일시", "발송예정일시", "받는사람", "세그먼트", "제목"])

    try:
        history_df = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        history_df = pd.DataFrame(columns=["발송일시", "세그먼트", "대상 인원", "메시지 요약", "상태"])
    completed_df = history_df[~history_df["상태"].astype(str).str.contains("예약")] if not history_df.empty else history_df

    with st.expander(f"발송 이력 — 예약 {len(scheduled_df)} · 완료 {len(completed_df)}", expanded=False):
        tab_scheduled, tab_completed = st.tabs([
            f"예약된 목록 ({len(scheduled_df)})", f"발송 완료 ({len(completed_df)})",
        ])
        with tab_scheduled:
            if scheduled_df.empty:
                st.caption("아직 예약된 이메일이 없습니다.")
            else:
                st.dataframe(scheduled_df.iloc[::-1], use_container_width=True, hide_index=True)
        with tab_completed:
            if completed_df.empty:
                st.caption("아직 발송 이력이 없습니다.")
            else:
                st.dataframe(completed_df.iloc[::-1], use_container_width=True, hide_index=True)
