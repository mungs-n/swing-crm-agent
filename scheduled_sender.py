"""
GitHub Actions가 매시간 실행하는 자동 발송 스크립트.
data/campaign_schedule.json 에 저장된 예약 설정을 확인해서,
지금이 그 조건(요일/시간/기간)에 맞으면 이메일을 발송한다.
"""

import json
import os
from datetime import datetime

import pandas as pd
import sendgrid
from sendgrid.helpers.mail import Mail

try:
    from zoneinfo import ZoneInfo
    KST = ZoneInfo("Asia/Seoul")
except ImportError:
    KST = None

SCHEDULE_FILE = "data/campaign_schedule.json"
HISTORY_FILE = "data/campaign_history.csv"
TEST_RECIPIENTS_FILE = "data/test_recipients.csv"
WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]


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


def main():
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            schedule = json.load(f)
    except FileNotFoundError:
        print("예약 설정 파일이 없습니다. 건너뜁니다.")
        return

    now = datetime.now(KST) if KST else datetime.now()
    today = now.date()
    today_str = today.strftime("%Y-%m-%d")
    weekday_label = WEEKDAY_LABELS[now.weekday()]

    start_date = datetime.strptime(schedule["start_date"], "%Y-%m-%d").date()
    end_date = datetime.strptime(schedule["end_date"], "%Y-%m-%d").date()
    start_time = datetime.strptime(schedule["start_time"], "%H:%M").time()
    end_time = datetime.strptime(schedule["end_time"], "%H:%M").time()

    conditions = {
        "기간 범위 안": start_date <= today <= end_date,
        "요일 일치": weekday_label in schedule["days"],
        "시간대 안": start_time <= now.time() <= end_time,
        "오늘 아직 미발송": today_str not in schedule.get("sent_dates", []),
    }
    print(f"[{now}] 조건 확인: {conditions}")

    if not all(conditions.values()):
        print("조건 미충족, 발송하지 않음.")
        return

    try:
        recipients = pd.read_csv(TEST_RECIPIENTS_FILE)
    except FileNotFoundError:
        print("data/test_recipients.csv 가 없습니다.")
        return

    if recipients.empty:
        print("수신자 목록이 비어있습니다.")
        return

    lines = schedule["copy"].split("\n")
    subject = lines[0].replace("제목: ", "").strip()
    body = "\n".join(lines[2:]).replace("본문: ", "").strip()

    success_count = 0
    fail_count = 0
    for _, row in recipients.iterrows():
        try:
            status = send_email(row["email"], subject, body)
            if status == 202:
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"발송 실패 ({row['email']}): {e}")
            fail_count += 1

    print(f"발송 완료: 성공 {success_count} / 실패 {fail_count}")

    save_history(
        schedule["segment"],
        schedule["copy"],
        len(recipients),
        f"예약 자동 발송 (성공 {success_count}/실패 {fail_count})"
    )

    schedule.setdefault("sent_dates", []).append(today_str)
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
