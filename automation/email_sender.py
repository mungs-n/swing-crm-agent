"""
담당자: 탭2 담당자 B
작업 내용: SendGrid 이메일 발송, 발송 이력 저장, 예약 발송 설정,
          반복 발송(매일/N일마다/특정 요일) 백그라운드 스케줄링
"""


import streamlit as st
import sendgrid
from sendgrid.helpers.mail import Attachment, ContentId, CustomArg, Disposition, FileContent, FileName, FileType, Mail
import pandas as pd
import base64
import mimetypes
import os
import uuid
from datetime import datetime, timedelta, time as dtime
from supabase import create_client

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except ImportError:
    KST = None


HISTORY_FILE = "data/campaign_history.csv"
TEST_RECIPIENTS_FILE = "data/test_recipients.csv"
SCHEDULED_FILE = "data/scheduled_emails.csv"
RECURRING_FILE = "data/recurring_campaigns.csv"
CAMPAIGN_ASSET_DIR = "data/campaign_assets"
FCM_TOKENS_FILE = "data/fcm_tokens.csv"

WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]  # Python weekday(): 월=0 ... 일=6
REPEATING_FREQS = {"매일 발송", "3일마다", "일주일마다", "특정 요일 반복"}
_FREQ_DELTAS = {
    "매일 발송": timedelta(days=1),
    "3일마다": timedelta(days=3),
    "일주일마다": timedelta(days=7),
}

os.makedirs(CAMPAIGN_ASSET_DIR, exist_ok=True)


def load_test_recipients():
    try:
        return pd.read_csv(TEST_RECIPIENTS_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return pd.DataFrame(columns=["name", "email"])
    except UnicodeDecodeError:
        # 엑셀에서 저장하면 한글 Windows 기본 인코딩(CP949)으로 저장되는 경우가 있음
        return pd.read_csv(TEST_RECIPIENTS_FILE, encoding="cp949")


# ==============================================================================
# 웹 푸시(FCM) 토큰 저장/조회
#
# 웹사이트(index.html)의 FCM 등록 스크립트가 토큰을 발급받으면, 별도로 띄운
# token_api.py 서버의 /api/fcm-token 엔드포인트로 전송한다. 그 서버가 이 함수를
# 호출해서 data/fcm_tokens.csv 에 저장하고, 캠페인 관리 화면에서 "웹 푸시" 채널을
# 고르면 이 파일에 쌓인 토큰들이 발송 대상이 된다.
# ==============================================================================

def save_fcm_token(token: str, user_id: str = "") -> None:
    """같은 토큰이 이미 저장돼 있으면 등록 시각만 갱신하고(중복 행 방지),
    처음 보는 토큰이면 새로 추가한다."""
    token = (token or "").strip()
    if not token:
        return

    try:
        df = pd.read_csv(FCM_TOKENS_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        df = pd.DataFrame(columns=["token", "user_id", "등록일시"])

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not df.empty and token in df["token"].astype(str).values:
        df.loc[df["token"] == token, "등록일시"] = now_str
        if user_id:
            df.loc[df["token"] == token, "user_id"] = user_id
    else:
        new_row = pd.DataFrame([{"token": token, "user_id": user_id, "등록일시": now_str}])
        df = pd.concat([df, new_row], ignore_index=True)

    os.makedirs(os.path.dirname(FCM_TOKENS_FILE), exist_ok=True)
    df.to_csv(FCM_TOKENS_FILE, index=False, encoding="utf-8-sig")


def load_fcm_tokens() -> pd.DataFrame:
    try:
        return pd.read_csv(FCM_TOKENS_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return pd.DataFrame(columns=["token", "user_id", "등록일시"])


def send_email(
    to_email, subject, body, send_at: int | None = None, send_id: str | None = None,
    image_bytes: bytes | None = None, image_name: str | None = None,
):
    """SendGrid로 이메일을 보낸다. send_at(미래 시각의 unix timestamp)을 주면 지금 API를
    호출은 하지만, 실제 발송은 SendGrid가 그 시각에 알아서 처리한다 (예약 발송,
    SendGrid 자체 제약으로 최대 72시간 이내만 가능).

    send_id를 주면 custom_args로 실어 보내서, SendGrid Event Webhook이 오픈/클릭 이벤트에
    이 값을 그대로 실어 돌려준다 (ingestion_server의 /sendgrid-events가 이 값으로
    campaign_sends 테이블의 어느 행을 업데이트할지 찾는다). campaign_sends에 해당
    send_id로 행을 미리 만들어두지 않으면 업데이트 대상이 없어 이벤트가 무시된다.

    image_bytes를 주면 본문 이미지로 인라인 첨부한다. base64 데이터 URI로 직접
    본문에 박아넣는 방식은 Gmail이 스팸/보안 이유로 대부분 걸러내서, SendGrid의
    정식 인라인 첨부(Content-ID 참조) 방식을 쓴다 - 메일 클라이언트 대부분에서
    안정적으로 보인다."""
    html = body.replace("\n", "<br>")
    if image_bytes:
        html += '<br><img src="cid:ab_test_image" style="max-width:100%">'

    sg = sendgrid.SendGridAPIClient(api_key=os.environ.get("SENDGRID_API_KEY"))
    message = Mail(
        from_email=os.environ.get("FROM_EMAIL"),
        to_emails=to_email,
        subject=subject,
        html_content=html,
    )
    if send_at is not None:
        message.send_at = send_at
    if send_id is not None:
        message.add_custom_arg(CustomArg("send_id", send_id))
    if image_bytes:
        mime_type = mimetypes.guess_type(image_name or "image.png")[0] or "image/png"
        message.attachment = Attachment(
            FileContent(base64.b64encode(image_bytes).decode()),
            FileName(image_name or "image.png"),
            FileType(mime_type),
            Disposition("inline"),
            ContentId("ab_test_image"),
        )
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
    message_summary = copy
    sent_at = datetime.now()

    new_row = {
        "campaign_id": campaign_id,
        "발송일시": sent_at.strftime("%Y-%m-%d %H:%M"),
        "세그먼트": segment,
        "대상 인원": count,
        # 예전엔 여기서 copy[:50] + "..." 로 잘라서 저장했는데, 그러면 나중에
        # "캠페인 이름 클릭 -> 상세 내용 보기" 같은 걸 할 때 원본 내용이 없어서
        # 다시 보여줄 수가 없었다. 목록 화면에서 길게 보이는 건 CSS로 줄여서
        # 표시하면 되니, 저장은 항상 전체 내용으로 한다.
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


# ==============================================================================
# 반복 발송(매일 / 3일마다 / 일주일마다 / 특정 요일 반복) 백그라운드 스케줄링
#
# Streamlit은 사용자가 화면과 상호작용할 때만 코드가 실행되기 때문에, "아무도
# 접속 안 해도 정해진 시각에 알아서 발송되는" 반복 캠페인을 만들려면 앱 프로세스
# 안에 별도의 백그라운드 스레드(APScheduler)를 하나 띄워둬야 한다.
#
# 이 스케줄러는 Streamlit 앱 프로세스가 켜져 있는 동안에만 동작한다. 서버가
# 재시작되거나 슬립 모드에 들어가는 배포 환경(예: 무료 티어)이라면 정확한 시각에
# 발송되지 않을 수 있다 — 그런 환경에서는 외부 cron이나 별도 워커 프로세스로
# 옮기는 게 더 안전하다.
# ==============================================================================

_RECURRING_COLUMNS = [
    "id", "segment", "channel", "subject", "body", "image_path",
    "freq", "weekdays", "send_time", "next_run", "created_at",
    "active", "send_count", "last_run",
]


def _empty_recurring_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_RECURRING_COLUMNS)


def load_recurring_campaigns() -> pd.DataFrame:
    try:
        df = pd.read_csv(RECURRING_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return _empty_recurring_df()
    if df.empty:
        return df
    # csv를 거치면서 bool이 문자열 "True"/"False"로 깨지는 경우가 있어 명시적으로 복구
    df["active"] = df["active"].astype(str).map(lambda v: v.strip().lower() == "true")
    return df


def _save_recurring_df(df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(RECURRING_FILE), exist_ok=True)
    df.to_csv(RECURRING_FILE, index=False, encoding="utf-8-sig")


def persist_campaign_image(uploaded_file, campaign_id: str) -> str:
    """업로드된 이미지를 디스크에 저장해서, 브라우저 세션이 끊긴 뒤에도
    백그라운드 스케줄러가 나중에 다시 읽어서 쓸 수 있게 한다."""
    if uploaded_file is None:
        return ""
    ext = os.path.splitext(getattr(uploaded_file, "name", ""))[1] or ".png"
    path = os.path.join(CAMPAIGN_ASSET_DIR, f"{campaign_id}{ext}")
    uploaded_file.seek(0)
    with open(path, "wb") as f:
        f.write(uploaded_file.read())
    return path


def _next_weekday_run(after_dt: datetime, weekdays: list[int], send_time: dtime) -> datetime:
    """after_dt 이후로 가장 가까운, weekdays(0=월 ... 6=일)에 속하는 날짜의
    send_time 시각을 찾는다."""
    if not weekdays:
        return after_dt + timedelta(days=7)
    for offset in range(1, 8):
        candidate_date = (after_dt + timedelta(days=offset)).date()
        if candidate_date.weekday() in weekdays:
            return datetime.combine(candidate_date, send_time)
    return after_dt + timedelta(days=7)


def compute_next_run(
    freq: str,
    after_dt: datetime,
    send_time: dtime,
    weekdays: list[int] | None = None,
) -> datetime:
    if freq == "특정 요일 반복":
        return _next_weekday_run(after_dt, weekdays or [], send_time)
    delta = _FREQ_DELTAS.get(freq, timedelta(days=1))
    return after_dt + delta


def register_recurring_campaign(
    segment: str,
    channel: str,
    subject: str,
    body: str,
    image_file,
    freq: str,
    first_run: datetime,
    send_time: dtime,
    weekdays: list[int] | None = None,
) -> str:
    """반복 캠페인을 등록한다. 실제 첫 발송을 포함한 모든 발송은
    백그라운드 스케줄러(_run_due_recurring_campaigns)가 next_run 시각이 되면 알아서 처리한다."""
    campaign_id = str(uuid.uuid4())[:8]
    image_path = persist_campaign_image(image_file, campaign_id)

    df = load_recurring_campaigns()
    new_row = {
        "id": campaign_id,
        "segment": segment,
        "channel": channel,
        "subject": subject,
        "body": body,
        "image_path": image_path,
        "freq": freq,
        "weekdays": ",".join(str(w) for w in (weekdays or [])),
        "send_time": send_time.strftime("%H:%M"),
        "next_run": first_run.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active": True,
        "send_count": 0,
        "last_run": "",
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    _save_recurring_df(df)
    return campaign_id


def set_recurring_active(campaign_id: str, active: bool) -> None:
    df = load_recurring_campaigns()
    if df.empty:
        return
    df.loc[df["id"] == campaign_id, "active"] = active
    _save_recurring_df(df)


def delete_recurring_campaign(campaign_id: str) -> None:
    df = load_recurring_campaigns()
    if df.empty:
        return
    row = df[df["id"] == campaign_id]
    if not row.empty:
        image_path = row.iloc[0].get("image_path", "")
        if image_path and os.path.exists(str(image_path)):
            try:
                os.remove(str(image_path))
            except OSError:
                pass
    df = df[df["id"] != campaign_id]
    _save_recurring_df(df)


def _run_due_recurring_campaigns() -> None:
    """지금 시각 기준으로 next_run이 지난 반복 캠페인들을 실제로 발송하고,
    다음 next_run을 다시 계산해서 저장한다.

    campaign_builder.py의 send_campaign_message를 여기서 지연 import한다.
    campaign_builder.py가 이 파일(email_sender.py)을 최상단에서 import하고
    있어서, 반대로 이 파일이 campaign_builder를 최상단에서 import하면 순환
    import가 되므로, 실제로 스케줄러 스레드가 도는 시점(함수 안)에서만 불러온다.
    """
    from automation.campaign_builder import send_campaign_message

    df = load_recurring_campaigns()
    if df.empty:
        return

    now = datetime.now()
    next_run_dt = pd.to_datetime(df["next_run"], errors="coerce")
    due_mask = df["active"] & next_run_dt.notna() & (next_run_dt <= now)
    if not due_mask.any():
        return

    for idx in df[due_mask].index:
        row = df.loc[idx]
        channel = row["channel"]
        subject = row["subject"]
        body = row["body"]
        image_path = str(row.get("image_path", "") or "")

        # 채널에 따라 수신자 소스가 다르다: 웹 푸시는 FCM 토큰, 나머지는 전화번호/이메일.
        try:
            if "웹 푸시" in channel:
                recipients = load_fcm_tokens()
            else:
                recipients = load_test_recipients()
        except Exception:
            recipients = pd.DataFrame()

        image_file = None
        if image_path and os.path.exists(image_path):
            try:
                image_file = open(image_path, "rb")
            except OSError:
                image_file = None

        success_count, fail_count = 0, 0
        try:
            for _, r in recipients.iterrows():
                if "웹 푸시" in channel:
                    target_info = r.get("token", "")
                else:
                    target_info = r.get("phone", r.get("email", ""))
                try:
                    status = send_campaign_message(channel, target_info, subject, body, image=image_file)
                    if status in (200, 202):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception:
                    fail_count += 1
        finally:
            if image_file:
                image_file.close()

        save_history(
            row["segment"],
            f"{subject}\n{body}",
            success_count,
            f"반복 발송 완료 ({channel} · {row['freq']} · {success_count + fail_count}명 중 {success_count}명 성공)",
            approval_mode="자동실행(반복)",
        )

        # 다음 실행 시각 계산
        try:
            send_time = datetime.strptime(str(row["send_time"]), "%H:%M").time()
        except ValueError:
            send_time = dtime(9, 0)
        weekdays_raw = str(row.get("weekdays", "") or "")
        weekdays = [int(w) for w in weekdays_raw.split(",") if w.strip() != ""]
        next_run = compute_next_run(row["freq"], now, send_time, weekdays)

        df.loc[idx, "next_run"] = next_run.strftime("%Y-%m-%d %H:%M:%S")
        df.loc[idx, "last_run"] = now.strftime("%Y-%m-%d %H:%M:%S")
        df.loc[idx, "send_count"] = int(row.get("send_count", 0) or 0) + 1

    _save_recurring_df(df)


def _start_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        st.warning(
            "반복 발송 기능을 쓰려면 APScheduler 패키지가 필요해요. "
            "터미널에서 `pip install apscheduler` 실행 후 앱을 다시 시작해주세요.",
        )
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        _run_due_recurring_campaigns,
        "interval",
        minutes=1,
        id="recurring_campaign_tick",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler


@st.cache_resource
def get_scheduler():
    """앱 프로세스 전체에서 딱 한 번만 실행되도록 cache_resource로 감싼다.
    세션마다(=사용자 접속마다) 새로 뜨면 스케줄러가 여러 개 생겨서
    캠페인이 중복 발송될 수 있어서 반드시 이렇게 싱글턴으로 관리해야 한다."""
    return _start_scheduler()


def render_recurring_campaigns_panel() -> None:
    """지금 돌고 있는 반복 캠페인을 보여주고, 일시정지/재개/삭제할 수 있게 한다."""
    df = load_recurring_campaigns()

    with st.expander("반복 발송 관리", expanded=False):
        if df.empty:
            st.caption("등록된 반복 캠페인이 없습니다.")
            return

        for _, row in df.iterrows():
            weekdays_raw = str(row.get("weekdays", "") or "")
            weekday_str = ""
            if row["freq"] == "특정 요일 반복" and weekdays_raw:
                idxs = [int(w) for w in weekdays_raw.split(",") if w.strip() != ""]
                weekday_str = " · " + ", ".join(WEEKDAY_LABELS[i] for i in idxs)

            active = bool(row["active"])
            dot_color = "#22C55E" if active else "#9CA3AF"
            status_label = "진행 중" if active else "일시정지"

            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            with c1:
                st.markdown(f"**{row['segment']}** · {row['channel']}{weekday_str}")
                st.caption(
                    f"{row['freq']} · 매회 {row['send_time']} · "
                    f"다음 발송: {row['next_run']} · 누적 {int(row['send_count'] or 0)}회"
                )
            with c2:
                st.markdown(
                    f"<span style='display:inline-block;width:7px;height:7px;border-radius:50%;"
                    f"background:{dot_color};margin-right:6px;'></span>"
                    f"<span style='font-size:0.85rem;color:#374151;'>{status_label}</span>",
                    unsafe_allow_html=True,
                )
            with c3:
                toggle_label = "일시정지" if active else "재개"
                if st.button(toggle_label, key=f"toggle_{row['id']}", use_container_width=True):
                    set_recurring_active(row["id"], not active)
                    st.rerun()
            with c4:
                if st.button("삭제", key=f"delete_{row['id']}", use_container_width=True):
                    delete_recurring_campaign(row["id"])
                    st.rerun()
            st.divider()