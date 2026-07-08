"""
가상 페르소나 기반 합성 데이터 생성 스크립트
실행: python data/generate_data.py
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import uuid

# 랜덤 시드 고정 (팀원 모두 같은 데이터 생성)
random.seed(42)
np.random.seed(42)

START_DATE = datetime(2024, 1, 1)
END_DATE = datetime(2024, 6, 28)
TOTAL_DAYS = (END_DATE - START_DATE).days

# 페르소나 행동 규칙 정의
PERSONA_RULES = {
    "신규_탐색자": {
        "count": 150,
        "sessions_per_week": (1, 4),
        "purchase_prob": 0.05,
        "avg_order_value": (15000, 40000),
        "discount_sensitivity": 0.3,
        "cart_abandon_rate": 0.70,
        "email_open_rate": 0.25,
    },
    "충동_구매자": {
        "count": 200,
        "sessions_per_week": (4, 8),
        "purchase_prob": 0.35,
        "avg_order_value": (20000, 60000),
        "discount_sensitivity": 0.05,
        "cart_abandon_rate": 0.25,
        "email_open_rate": 0.40,
    },
    "할인_구매자": {
        "count": 200,
        "sessions_per_week": (1, 3),
        "purchase_prob": 0.06,
        "avg_order_value": (80000, 200000),
        "discount_sensitivity": 0.70,
        "cart_abandon_rate": 0.80,
        "email_open_rate": 0.65,
    },
    "브랜드_충성_고객": {
        "count": 150,
        "sessions_per_week": (2, 5),
        "purchase_prob": 0.25,
        "avg_order_value": (50000, 120000),
        "discount_sensitivity": 0.10,
        "cart_abandon_rate": 0.30,
        "email_open_rate": 0.55,
    },
    "이탈_위험_고객": {
        "count": 150,
        "sessions_per_week": (0, 2),
        "purchase_prob": 0.04,
        "avg_order_value": (30000, 80000),
        "discount_sensitivity": 0.40,
        "cart_abandon_rate": 0.85,
        "email_open_rate": 0.20,
    },
    "휴면_고객": {
        "count": 150,
        "sessions_per_week": (0, 1),
        "purchase_prob": 0.01,
        "avg_order_value": (20000, 60000),
        "discount_sensitivity": 0.50,
        "cart_abandon_rate": 0.95,
        "email_open_rate": 0.10,
    },
}

CATEGORIES = ["상의", "하의", "아우터", "원피스", "신발", "가방", "액세서리"]
GENDERS = ["F", "M"]
CHANNELS = ["SNS", "검색광고", "직접유입", "이메일"]
AGES = list(range(20, 55))


def generate_users():
    users = []
    for persona, rules in PERSONA_RULES.items():
        for _ in range(rules["count"]):
            signup_date = START_DATE + timedelta(
                days=random.randint(0, TOTAL_DAYS)
            )
            users.append({
                "user_id": str(uuid.uuid4())[:8],
                "persona_type": persona,
                "age": random.choice(AGES),
                "gender": random.choice(GENDERS),
                "acquisition_channel": random.choice(CHANNELS),
                "signup_date": signup_date.strftime("%Y-%m-%d"),
            })
    return pd.DataFrame(users)


def generate_orders_and_events(users_df):
    orders = []
    events = []

    for _, user in users_df.iterrows():
        persona = user["persona_type"]
        rules = PERSONA_RULES[persona]

        signup_date = datetime.strptime(user["signup_date"], "%Y-%m-%d")
        active_days = (END_DATE - signup_date).days
        if active_days <= 0:
            continue

        # 세션 수 결정
        weeks = active_days / 7
        sessions_per_week = random.uniform(*rules["sessions_per_week"])
        total_sessions = int(weeks * sessions_per_week)

        for _ in range(total_sessions):
            session_date = signup_date + timedelta(
                days=random.randint(0, active_days)
            )
            session_id = str(uuid.uuid4())[:8]
            category = random.choice(CATEGORIES)

            # page_view 이벤트
            events.append({
                "user_id": user["user_id"],
                "session_id": session_id,
                "timestamp": session_date.strftime("%Y-%m-%d %H:%M:%S"),
                "event_type": "page_view",
                "product_id": str(uuid.uuid4())[:6],
                "category": category,
                "price": None,
            })

            # product_view
            if random.random() > 0.3:
                price = random.randint(*[int(x) for x in rules["avg_order_value"]])
                events.append({
                    "user_id": user["user_id"],
                    "session_id": session_id,
                    "timestamp": session_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "event_type": "product_view",
                    "product_id": str(uuid.uuid4())[:6],
                    "category": category,
                    "price": price,
                })

                # add_to_cart
                if random.random() > rules["cart_abandon_rate"]:
                    events.append({
                        "user_id": user["user_id"],
                        "session_id": session_id,
                        "timestamp": session_date.strftime("%Y-%m-%d %H:%M:%S"),
                        "event_type": "add_to_cart",
                        "product_id": str(uuid.uuid4())[:6],
                        "category": category,
                        "price": price,
                    })

                    # purchase
                    if random.random() < rules["purchase_prob"]:
                        order_amount = random.randint(
                            *[int(x) for x in rules["avg_order_value"]]
                        )
                        discount = random.randint(0, int(order_amount * 0.3))
                        order_id = str(uuid.uuid4())[:8]

                        events.append({
                            "user_id": user["user_id"],
                            "session_id": session_id,
                            "timestamp": session_date.strftime("%Y-%m-%d %H:%M:%S"),
                            "event_type": "purchase",
                            "product_id": str(uuid.uuid4())[:6],
                            "category": category,
                            "price": order_amount,
                        })

                        orders.append({
                            "order_id": order_id,
                            "user_id": user["user_id"],
                            "order_date": session_date.strftime("%Y-%m-%d"),
                            "total_amount": order_amount,
                            "discount_amount": discount,
                            "coupon_used": discount > 0,
                            "category": category,
                        })

    return pd.DataFrame(orders), pd.DataFrame(events)


if __name__ == "__main__":
    print("📊 데이터 생성 시작...")

    print("👥 유저 데이터 생성 중...")
    users_df = generate_users()
    users_df.to_csv("data/users.csv", index=False)
    print(f"   ✅ users.csv 생성 완료 ({len(users_df)}명)")

    print("🛒 주문/이벤트 데이터 생성 중... (시간이 좀 걸려요)")
    orders_df, events_df = generate_orders_and_events(users_df)
    orders_df.to_csv("data/orders.csv", index=False)
    events_df.to_csv("data/events.csv", index=False)
    print(f"   ✅ orders.csv 생성 완료 ({len(orders_df)}건)")
    print(f"   ✅ events.csv 생성 완료 ({len(events_df)}건)")

    print("\n🎉 데이터 생성 완료!")
