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

# 기존 고객 비율: 분석 기간 시작 이전에 이미 가입해 있던 고객 비중.
# 이 비율이 없으면 가입일이 6개월 구간에 균등 분포되어, 관찰 기간 초반에는
# "아직 가입 안 한" 고객이 대다수라 활동 고객 수가 시간이 갈수록 인위적으로 급증한다.
EXISTING_CUSTOMER_RATIO = 0.75
SIGNUP_LOOKBACK_DAYS = 730  # 기존 고객의 가입일을 분석 시작일 이전 최대 2년까지 분산

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

CATEGORIES = ["레깅스", "조거팬츠", "상의", "아우터", "하의", "이너웨어", "스윔웨어", "슈즈"]

# 성별 비중: 여성 60%대
GENDER_WEIGHTS = {"F": 0.64, "M": 0.36}

# 연령대 비중: 20대 > 30대 > 40대 > 50대 > 10대 / 60대 이상 순으로 명확한 순위 (성별과는 독립적으로 샘플링)
AGE_GROUPS = [
    ("10대", (15, 19), 0.06),
    ("20대", (20, 29), 0.34),
    ("30대", (30, 39), 0.28),
    ("40대", (40, 49), 0.17),
    ("50대", (50, 59), 0.10),
    ("60대 이상", (60, 75), 0.05),
]

# 유입 채널 비중: SNS 최다(→매출 최고) / 이메일 최소(→매출 최저), 값이 서로 겹치지 않게 구성
CHANNEL_WEIGHTS = {
    "SNS": 0.32,
    "direct": 0.24,
    "search_ad": 0.17,
    "referral": 0.16,
    "email": 0.11,
}

SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
            "한", "오", "서", "신", "권", "황", "안", "송", "전", "홍"]
GIVEN_NAME_1 = ["서", "민", "지", "하", "도", "시", "예", "유", "준", "현",
                "재", "은", "소", "다", "태", "진", "우", "성", "수", "채"]
GIVEN_NAME_2 = ["연", "우", "윤", "준", "서", "현", "민", "호", "원", "진",
                "아", "은", "율", "린", "안", "혁", "빈", "훈", "경", "영"]


def sample_gender():
    """가중치 기반 성별 샘플링 (여성 60%대)"""
    return random.choices(list(GENDER_WEIGHTS.keys()), weights=list(GENDER_WEIGHTS.values()))[0]


def sample_age():
    """가중치 기반 연령대 샘플링 (20대 > 30대 > 40대 > 50대 > 10대/60대 이상)"""
    r = random.random()
    cum = 0.0
    for _, (lo, hi), weight in AGE_GROUPS:
        cum += weight
        if r < cum:
            return random.randint(lo, hi)
    lo, hi = AGE_GROUPS[-1][1]
    return random.randint(lo, hi)


def sample_channel():
    """가중치 기반 유입 채널 샘플링"""
    channels = list(CHANNEL_WEIGHTS.keys())
    weights = list(CHANNEL_WEIGHTS.values())
    return random.choices(channels, weights=weights)[0]


def sample_korean_name():
    """실제 있을 법한 한국 이름 샘플링 (성 + 이름 2음절)"""
    return random.choice(SURNAMES) + random.choice(GIVEN_NAME_1) + random.choice(GIVEN_NAME_2)


# 레깅스가 브랜드 시그니처 상품이라 연중 항상 제일 잘 팔리고, 아우터/스윔웨어는 계절 성수기에도
# 연간 합계로는 주력 상품을 넘어서지 않도록 기본 판매력을 다르게 둔다.
CATEGORY_BASE_WEIGHT = {
    "레깅스": 1.7,
    "상의": 1.35,
    "조거팬츠": 1.25,
    "하의": 1.15,
    "이너웨어": 0.95,
    "슈즈": 0.85,
}


def category_weights(month):
    """월별 카테고리 선택 가중치. 아우터: 1월 피크 후 감소 / 스윔웨어: 6월 피크로 증가.
    두 계절 카테고리 모두 성수기에도 레깅스 등 주력 상품의 연간 합계는 넘지 않는 수준으로 제한하고,
    세션마다 랜덤 흔들림을 섞어 완전히 매끈한 직선 추세가 되지 않게 한다."""
    outer_mult = max(0.4, 1.8 - (month - 1) * 0.28)
    # 스윔웨어는 SKU 자체가 적은 니치 카테고리라는 설정 → 6월 성수기에도 연간 합계는 하위권에 머물게 낮게 잡는다
    swim_mult = min(1.6, 0.25 + (month - 1) * 0.27)
    weights = []
    for cat in CATEGORIES:
        if cat == "아우터":
            base = outer_mult
        elif cat == "스윔웨어":
            base = swim_mult
        else:
            base = CATEGORY_BASE_WEIGHT[cat]
        weights.append(base * random.uniform(0.8, 1.25))
    return weights


def random_timestamp(base_date):
    random_time = timedelta(
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59)
    )
    return (base_date + random_time).strftime("%Y-%m-%d %H:%M:%S")


def sample_individual_prob(base_prob, spread_ratio=0.3):
    """페르소나 평균 확률(base_prob) 주변에 유저별 개인차를 부여 (평균은 base_prob로 유지)"""
    val = random.gauss(base_prob, base_prob * spread_ratio)
    return min(max(val, 0.0), 1.0)


def sample_signup_date():
    """가입일 샘플링: 대부분은 분석 기간 이전부터 있던 기존 고객, 일부는 기간 내 신규 가입"""
    if random.random() < EXISTING_CUSTOMER_RATIO:
        return START_DATE - timedelta(days=random.randint(1, SIGNUP_LOOKBACK_DAYS))
    return START_DATE + timedelta(days=random.randint(0, TOTAL_DAYS))


def generate_users():
    users = []
    for persona, rules in PERSONA_RULES.items():
        for _ in range(rules["count"]):
            signup_date = sample_signup_date()
            gender, age = sample_gender(), sample_age()
            users.append({
                "user_id": str(uuid.uuid4())[:8],
                "name": sample_korean_name(),
                "persona_type": persona,
                "age": age,
                "gender": gender,
                "acquisition_channel": sample_channel(),
                "signup_date": signup_date.strftime("%Y-%m-%d"),
                "_purchase_prob": sample_individual_prob(rules["purchase_prob"]),
            })
    return pd.DataFrame(users)


def generate_orders_and_events(users_df):
    orders = []
    events = []

    for _, user in users_df.iterrows():
        persona = user["persona_type"]
        rules = PERSONA_RULES[persona]

        signup_date = datetime.strptime(user["signup_date"], "%Y-%m-%d")
        # 활동은 가입일과 분석 시작일 중 늦은 시점부터 생성 (기존 고객은 분석 기간 전체에 걸쳐 균등 분포)
        activity_start = max(signup_date, START_DATE)
        active_days = (END_DATE - activity_start).days
        if active_days <= 0:
            continue

        weeks = active_days / 7
        sessions_per_week = random.uniform(*rules["sessions_per_week"])
        total_sessions = int(weeks * sessions_per_week)

        for _ in range(total_sessions):
            session_date = activity_start + timedelta(days=random.randint(0, active_days))
            session_id = str(uuid.uuid4())[:8]
            category = random.choices(CATEGORIES, weights=category_weights(session_date.month))[0]
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

                    # purchase (페르소나 평균이 아니라 유저별 개인 확률 사용)
                    if random.random() < user["_purchase_prob"]:
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

    print("Creating orders and events...")
    orders_df, events_df = generate_orders_and_events(users_df)
    # utf-8-sig로 저장해야 엑셀에서 열었을 때 한글이 깨지지 않음 (BOM 없는 utf-8은 엑셀이 CP949로 오인식)
    orders_df.to_csv("data/orders.csv", index=False, encoding="utf-8-sig")
    events_df.to_csv("data/events.csv", index=False, encoding="utf-8-sig")
    print(f"orders.csv done ({len(orders_df)} orders)")

    users_df.drop(columns=["_purchase_prob"]).to_csv("data/users.csv", index=False, encoding="utf-8-sig")
    print(f"users.csv done ({len(users_df)} users)")
    print(f"events.csv done ({len(events_df)} events)")

    print("Done!")