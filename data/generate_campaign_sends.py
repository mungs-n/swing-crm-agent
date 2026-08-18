"""
campaign_sends.csv 생성 스크립트
캠페인 1건 발송 = 1행으로, 채널별 오픈/클릭/전환 이벤트를 시뮬레이션한다.
기존 users.csv/orders.csv는 읽기만 하고 수정하지 않는다.
"""

import random
import uuid
from datetime import datetime, timedelta

import pandas as pd

random.seed(7)

START_DATE = datetime(2026, 1, 1)
END_DATE = datetime(2026, 6, 28)

# generate_data.py의 PERSONA_RULES와 동일한 페르소나별 이메일 오픈율.
# users.csv에는 저장되지 않는 값이라 여기서 동일하게 재정의해서 재사용한다.
EMAIL_OPEN_RATE = {
    "new_explorer": 0.25,
    "impulsive_buyer": 0.40,
    "discount_hunter": 0.70,
    "brand_loyalist": 0.55,
    "churn_risk": 0.15,
    "dormant": 0.05,
}

PERSONA_KR = {
    "new_explorer": "신규 탐색자",
    "impulsive_buyer": "충동 구매자",
    "discount_hunter": "할인 구매자",
    "brand_loyalist": "브랜드 충성 고객",
    "churn_risk": "이탈 위험 고객",
    "dormant": "휴면 고객",
}

# 채널별로 오픈/클릭 추적 가능 여부가 다르다 (친구 스펙 그대로 반영).
# open_trackable: 오픈 이벤트를 신뢰성 있게 수집 가능한지 (이메일만)
# click_trackable: 클릭 이벤트를 수집 가능한지 (이메일/웹푸시/웹팝업)
CHANNELS = {
    "email": {"weight": 0.55, "open_trackable": True, "click_trackable": True},
    "kakao": {"weight": 0.20, "open_trackable": False, "click_trackable": False},
    "sms": {"weight": 0.10, "open_trackable": False, "click_trackable": False},
    "webpush": {"weight": 0.10, "open_trackable": False, "click_trackable": True},
    "webpopup": {"weight": 0.05, "open_trackable": False, "click_trackable": True},
}

N_CAMPAIGNS = 28


def load_source_data():
    users = pd.read_csv("data/users.csv", encoding="utf-8-sig")
    orders = pd.read_csv("data/orders.csv", encoding="utf-8-sig")
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    return users, orders


def random_dt(base_date):
    return base_date + timedelta(
        hours=random.randint(0, 23), minutes=random.randint(0, 59)
    )


def pick_channel():
    channels = list(CHANNELS.keys())
    weights = [CHANNELS[c]["weight"] for c in channels]
    return random.choices(channels, weights=weights)[0]


def simulate_campaign(users, orders, campaign_id, persona, channel, sent_date, rows):
    recipients = users[users["persona_type"] == persona]
    if recipients.empty:
        return
    sample_size = max(1, int(len(recipients) * random.uniform(0.5, 0.9)))
    recipients = recipients.sample(n=sample_size, random_state=random.randint(0, 10_000))

    props = CHANNELS[channel]
    open_rate = EMAIL_OPEN_RATE[persona]

    for _, user in recipients.iterrows():
        sent_at = random_dt(sent_date)
        delivered = random.random() < 0.96

        opened_at = None
        clicked_at = None
        converted_order_id = None
        conversion_type = None
        revenue = None

        if delivered:
            if props["open_trackable"] and random.random() < open_rate:
                opened_at = sent_at + timedelta(minutes=random.randint(2, 600))

            if props["click_trackable"]:
                # 이메일은 오픈한 사람 중 일부만 클릭, 웹푸시/웹팝업은 오픈 개념 없이 바로 클릭
                click_base = (opened_at is not None) if props["open_trackable"] else True
                if click_base and random.random() < 0.32:
                    click_from = opened_at if opened_at else sent_at
                    clicked_at = click_from + timedelta(minutes=random.randint(1, 180))

            # 전환 판정: 클릭 가능 채널은 클릭 후 72시간 이내, 클릭 불가 채널(카카오/문자)은
            # 발송 후 7일 이내 구매를 이 캠페인 덕분으로 느슨하게 인정(attributed)
            if props["click_trackable"]:
                if clicked_at is not None:
                    window_start, window_end = clicked_at, clicked_at + timedelta(hours=72)
                    ctype = "direct"
                else:
                    window_start = window_end = None
                    ctype = None
            else:
                window_start, window_end = sent_at, sent_at + timedelta(days=7)
                ctype = "attributed"

            if window_start is not None:
                user_orders = orders[
                    (orders["user_id"] == user["user_id"])
                    & (orders["order_date"] >= window_start)
                    & (orders["order_date"] <= window_end)
                ].sort_values("order_date")
                if not user_orders.empty:
                    matched = user_orders.iloc[0]
                    converted_order_id = matched["order_id"]
                    conversion_type = ctype
                    revenue = int(matched["total_amount"])

        rows.append(
            {
                "send_id": str(uuid.uuid4())[:8],
                "campaign_id": campaign_id,
                "user_id": user["user_id"],
                "segment": PERSONA_KR[persona],
                "channel": channel,
                "sent_at": sent_at.strftime("%Y-%m-%d %H:%M:%S"),
                "delivered": delivered,
                "opened_at": opened_at.strftime("%Y-%m-%d %H:%M:%S") if opened_at else "",
                "clicked_at": clicked_at.strftime("%Y-%m-%d %H:%M:%S") if clicked_at else "",
                "converted_order_id": converted_order_id or "",
                "conversion_type": conversion_type or "",
                "revenue": revenue if revenue is not None else "",
            }
        )


def main():
    users, orders = load_source_data()
    personas = list(EMAIL_OPEN_RATE.keys())

    rows = []
    history_rows = []
    for i in range(N_CAMPAIGNS):
        campaign_id = str(uuid.uuid4())[:8]
        persona = random.choice(personas)
        channel = pick_channel()
        # 캠페인 시작일은 관찰 시작일(01-01) 이후 15일부터 분포시켜, 발송 이전 기간이
        # 필요한 향후 베이스라인 비교 분석에서 계산 불가 케이스가 과도하게 많아지지 않도록 함
        span_days = (END_DATE - (START_DATE + timedelta(days=15))).days
        sent_date = START_DATE + timedelta(days=15 + random.randint(0, span_days))
        before = len(rows)
        simulate_campaign(users, orders, campaign_id, persona, channel, sent_date, rows)
        sent_count = len(rows) - before
        if sent_count == 0:
            continue
        # campaign_sends.csv가 참조하는 campaign_id가 campaign_history.csv에도 있어야
        # 외래키가 성립하므로, 합성 캠페인마다 요약 행도 같이 만든다 (기존 실사용 11개 행은
        # 건드리지 않고 새 행만 추가).
        history_rows.append(
            {
                "campaign_id": campaign_id,
                "발송일시": sent_date.strftime("%Y-%m-%d %H:%M"),
                "세그먼트": PERSONA_KR[persona],
                "대상 인원": sent_count,
                "메시지 요약": f"[합성 데이터] {PERSONA_KR[persona]} 대상 {channel} 캠페인",
                "상태": "전체 발송 완료 (합성 데이터)",
                "approval_mode": "자동실행",
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv("data/campaign_sends.csv", index=False, encoding="utf-8-sig")
    print(f"campaign_sends.csv done ({len(df)} rows, {len(history_rows)} campaigns)")
    print(df["channel"].value_counts())
    print("전환 건수:", (df["converted_order_id"] != "").sum())

    history_new = pd.DataFrame(history_rows)
    try:
        history_existing = pd.read_csv("data/campaign_history.csv", encoding="utf-8-sig")
        history_combined = pd.concat([history_existing, history_new], ignore_index=True)
    except FileNotFoundError:
        history_combined = history_new
    history_combined.to_csv("data/campaign_history.csv", index=False, encoding="utf-8-sig")
    print(f"campaign_history.csv에 합성 캠페인 {len(history_new)}건 추가 (기존 행은 유지)")


if __name__ == "__main__":
    main()
