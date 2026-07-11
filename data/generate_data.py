import random
import numpy as np
import pandas as pd
import uuid
from datetime import datetime, timedelta
import os
os.makedirs("data", exist_ok=True)

random.seed(42)
np.random.seed(42)

START_DATE = datetime(2026, 1, 1)
END_DATE = datetime(2026, 6, 28)
TOTAL_DAYS = (END_DATE - START_DATE).days

PERSONA_RULES = {
    "new_explorer": {
        "count": 150,
        "sessions_per_week": (1, 4),
        "purchase_prob": 0.05,
        "avg_order_value": (30000, 80000),
        "discount_sensitivity": 0.3,
        "cart_abandon_rate": 0.70,
        "email_open_rate": 0.25,
    },
    "impulsive_buyer": {
        "count": 200,
        "sessions_per_week": (2, 7),
        "purchase_prob": 0.35,
        "avg_order_value": (50000, 100000),
        "discount_sensitivity": 0.1,
        "cart_abandon_rate": 0.25,
        "email_open_rate": 0.40,
    },
    "discount_hunter": {
        "count": 200,
        "sessions_per_week": (1, 3),
        "purchase_prob": 0.10,
        "avg_order_value": (80000, 150000),
        "discount_sensitivity": 0.8,
        "cart_abandon_rate": 0.65,
        "email_open_rate": 0.70,
    },
    "brand_loyalist": {
        "count": 150,
        "sessions_per_week": (4, 6),
        "purchase_prob": 0.25,
        "avg_order_value": (80000, 250000),
        "discount_sensitivity": 0.15,
        "cart_abandon_rate": 0.30,
        "email_open_rate": 0.55,
    },
    "churn_risk": {
        "count": 150,
        "sessions_per_week": (0, 2),
        "purchase_prob": 0.03,
        "avg_order_value": (30000, 80000),
        "discount_sensitivity": 0.40,
        "cart_abandon_rate": 0.85,
        "email_open_rate": 0.15,
    },
    "dormant": {
        "count": 150,
        "sessions_per_week": (0, 1),
        "purchase_prob": 0.01,
        "avg_order_value": (30000, 60000),
        "discount_sensitivity": 0.05,
        "cart_abandon_rate": 0.95,
        "email_open_rate": 0.05,
    },
}

CATEGORIES = ["tops", "bottoms", "outerwear", "dress", "shoes", "bags", "accessories", "underwear"]
GENDERS = ["F", "M"]
CHANNELS = ["SNS", "search_ad", "direct", "email", "referral"]
AGES = list(range(20, 60))


def random_timestamp(base_date):
    random_time = timedelta(
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59)
    )
    return (base_date + random_time).strftime("%Y-%m-%d %H:%M:%S")


def generate_users():
    users = []
    for persona, rules in PERSONA_RULES.items():
        for _ in range(rules["count"]):
            signup_date = START_DATE + timedelta(days=random.randint(0, TOTAL_DAYS))
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

        weeks = active_days / 7
        sessions_per_week = random.uniform(*rules["sessions_per_week"])
        total_sessions = int(weeks * sessions_per_week)

        for _ in range(total_sessions):
            session_date = signup_date + timedelta(days=random.randint(0, active_days))
            session_id = str(uuid.uuid4())[:8]
            category = random.choice(CATEGORIES)
            ts = random_timestamp(session_date)

            # page_view
            events.append({
                "user_id": user["user_id"],
                "session_id": session_id,
                "timestamp": ts,
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
                    "timestamp": ts,
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
                        "timestamp": ts,
                        "event_type": "add_to_cart",
                        "product_id": str(uuid.uuid4())[:6],
                        "category": category,
                        "price": price,
                    })

                    # purchase
                    if random.random() < rules["purchase_prob"]:
                        order_amount = random.randint(*[int(x) for x in rules["avg_order_value"]])
                        product_id = str(uuid.uuid4())[:6]
                        order_id = str(uuid.uuid4())[:8]

                        # coupon_used: 할인 민감도 기반
                        coupon_used = random.random() < min(rules["discount_sensitivity"] + 0.2, 0.9)
                        discount = random.randint(
                            int(order_amount * 0.05),
                            int(order_amount * 0.3)
                        ) if coupon_used else 0

                        events.append({
                            "user_id": user["user_id"],
                            "session_id": session_id,
                            "timestamp": ts,
                            "event_type": "purchase",
                            "product_id": product_id,
                            "category": category,
                            "price": order_amount,
                        })

                        orders.append({
                            "order_id": order_id,
                            "user_id": user["user_id"],
                            "order_date": session_date.strftime("%Y-%m-%d"),
                            "total_amount": order_amount,
                            "discount_amount": discount,
                            "coupon_used": coupon_used,
                            "category": category,
                        })

                        # review_write (30% of buyers)
                        if random.random() < 0.3:
                            events.append({
                                "user_id": user["user_id"],
                                "session_id": session_id,
                                "timestamp": ts,
                                "event_type": "review_write",
                                "product_id": product_id,
                                "category": category,
                                "price": None,
                            })

    return pd.DataFrame(orders), pd.DataFrame(events)


if __name__ == "__main__":
    print("Generating data...")

    print("Creating users...")
    users_df = generate_users()
    users_df.to_csv("data/users.csv", index=False)
    print(f"users.csv done ({len(users_df)} users)")

    print("Creating orders and events...")
    orders_df, events_df = generate_orders_and_events(users_df)
    orders_df.to_csv("data/orders.csv", index=False)
    events_df.to_csv("data/events.csv", index=False)
    print(f"orders.csv done ({len(orders_df)} orders)")
    print(f"events.csv done ({len(events_df)} events)")

    print("Done!")