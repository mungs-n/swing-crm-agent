"""
담당자: 탭1 AI 인사이트 담당
작업 내용: Claude API 연동(도구 호출 기반 챗봇)
"""

import json
import streamlit as st
import anthropic
import os
import pandas as pd

from utils.rfm import calculate_rfm, assign_segment


# ---------------------------------------------------------
# 데이터 로드 & 지표 계산
# ---------------------------------------------------------

@st.cache_data
def load_data():
    """CSV 로드 (캐싱됨). data/*.csv 파일을 직접 교체했다면
    Streamlit 메뉴의 'Clear cache' 또는 앱 재시작으로 캐시를 갱신해야 한다.

    encoding='utf-8-sig': 데이터 생성 스크립트가 엑셀 호환을 위해 BOM을
    포함해서 저장하므로, 명시하지 않으면 pandas/환경에 따라 첫 컬럼명에
    BOM 문자가 섞여 들어갈 수 있다."""
    users = pd.read_csv("data/users.csv", encoding="utf-8-sig")
    orders = pd.read_csv("data/orders.csv", encoding="utf-8-sig")
    events = pd.read_csv("data/events.csv", encoding="utf-8-sig")
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    events["timestamp"] = pd.to_datetime(events["timestamp"])
    return users, orders, events


def compute_repeat_purchase_rate(orders: pd.DataFrame) -> float:
    """2회 이상 구매한 고객 비율(%). (구매 이력이 있는 고객 중 기준)"""
    if orders.empty:
        return 0.0
    order_counts = orders.groupby("user_id").size()
    if order_counts.shape[0] == 0:
        return 0.0
    return (order_counts >= 2).sum() / order_counts.shape[0] * 100


def compute_cohort_retention(users: pd.DataFrame, orders_full: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    """가입월 기준 코호트 리텐션(%) 피벗 테이블 계산

    코호트 = 회원가입 월. 각 코호트 전체 인원 대비, 가입 후 N개월차에
    '구매'가 있었던 회원 비율(%)을 계산한다.

    orders_full은 반드시 기간으로 잘라내지 않은 전체 주문 이력이어야 한다. 선택한
    기간으로 자른 주문만 넘기면, 그 기간이 짧을수록 'N개월 후 시점 자체가 관측 기간
    안에 없어서 데이터가 없는 것'과 '실제로 재구매하지 않은 것'을 구분할 수 없어서
    재구매율이 실제보다 낮게(짧은 기간에서는 0%로) 잘못 계산된다.

    start/end를 주면 그 기간에 가입한 코호트만 포함하고, 생략하면 orders_full의
    관측 시작월 이후 가입자 전체를 코호트로 포함한다.
    """
    if orders_full.empty:
        return pd.DataFrame()

    users = users.copy()
    orders_full = orders_full.copy()

    users["signup_date"] = pd.to_datetime(users["signup_date"])
    users["cohort_month"] = users["signup_date"].dt.to_period("M")

    if start is not None and end is not None:
        start_month = pd.Timestamp(start).to_period("M")
        end_month = pd.Timestamp(end).to_period("M")
        users = users[(users["cohort_month"] >= start_month) & (users["cohort_month"] <= end_month)]
    else:
        obs_start_month = orders_full["order_date"].min().to_period("M")
        users = users[users["cohort_month"] >= obs_start_month]

    merged = orders_full.merge(users[["user_id", "cohort_month"]], on="user_id", how="inner")
    if merged.empty:
        return pd.DataFrame()

    merged["order_month"] = merged["order_date"].dt.to_period("M")
    merged["months_since"] = (
        merged["order_month"] - merged["cohort_month"]
    ).apply(lambda x: x.n)
    merged = merged[merged["months_since"] >= 0]

    cohort_sizes = users.groupby("cohort_month")["user_id"].nunique()
    active_counts = (
        merged.groupby(["cohort_month", "months_since"])["user_id"]
        .nunique()
        .reset_index()
    )
    pivot = active_counts.pivot(
        index="cohort_month", columns="months_since", values="user_id"
    )
    retention = pivot.divide(cohort_sizes, axis=0) * 100
    return retention.sort_index()


def get_period_slices(users: pd.DataFrame, orders: pd.DataFrame, events: pd.DataFrame, start_date, end_date):
    """get_dashboard_summary()와 recommend_segment()에서 똑같이 반복되던 기간
    필터링을 한 곳으로 모은 헬퍼. 이 부분만 고치면 두 함수 모두에 반영되므로,
    한쪽만 고치고 다른 쪽을 깜빡하는 종류의 버그(과거 total_users 건)를 막는다.

    period_orders / period_events: 선택한 기간(start_date~end_date) 안의 데이터만.

    주의: users["signup_date"]/orders["order_date"]/events["timestamp"]가 이미
    datetime으로 변환돼 있다고 가정한다 (호출 전에 변환해서 넘길 것).
    """
    period_orders = orders[(orders["order_date"].dt.date >= start_date) & (orders["order_date"].dt.date <= end_date)]
    period_events = events[(events["timestamp"].dt.date >= start_date) & (events["timestamp"].dt.date <= end_date)]
    return period_orders, period_events


# ---------------------------------------------------------
# 챗봇 도구(tool) 함수들 — Claude는 이 함수들을 "호출"해서 얻은 값만 답변에
# 인용할 수 있고, 숫자를 스스로 계산/추정할 수 없다. 모든 함수는 JSON으로
# 직렬화 가능한 dict를 반환한다.
# ---------------------------------------------------------

DATASET_MIN_DATE = "2026-01-01"
DATASET_MAX_DATE = "2026-06-28"

CHANNEL_KR = {"SNS": "SNS", "search_ad": "검색광고", "direct": "직접유입", "email": "이메일", "referral": "추천"}
PERSONA_KR = {
    "new_explorer": "신규 탐색자",
    "impulsive_buyer": "충동 구매자",
    "discount_hunter": "할인 헌터",
    "brand_loyalist": "브랜드 충성 고객",
    "churn_risk": "이탈 위험 고객",
    "dormant": "휴면 고객",
}


def tool_get_kpi_summary(start_date: str, end_date: str) -> dict:
    """특정 기간의 GMV/AOV/주문 건수/활성 고객 수/구매 전환율/장바구니 이탈률/재구매율"""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, period_events = get_period_slices(users, orders, events, start, end)

    gmv = period_orders["total_amount"].sum()
    order_count = len(period_orders)
    aov = gmv / order_count if order_count > 0 else 0

    purchase_users = period_events[period_events["event_type"] == "purchase"]["user_id"].nunique()
    all_visitors = period_events["user_id"].nunique()
    conversion_rate = (purchase_users / all_visitors * 100) if all_visitors > 0 else 0

    cart_users = period_events[period_events["event_type"] == "add_to_cart"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    repeat_purchase_rate = compute_repeat_purchase_rate(period_orders)

    return {
        "기간": f"{start_date} ~ {end_date}",
        "GMV": int(gmv),
        "AOV": int(aov),
        "주문_건수": order_count,
        "활성_고객_수": int(all_visitors),
        "구매_전환율_퍼센트": round(conversion_rate, 1),
        "장바구니_이탈률_퍼센트": round(cart_abandon_rate, 1),
        "재구매율_퍼센트": round(repeat_purchase_rate, 1),
    }


def tool_get_category_breakdown(start_date: str, end_date: str) -> dict:
    """특정 기간의 카테고리별 매출 순위"""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, _ = get_period_slices(users, orders, events, start, end)
    if period_orders.empty:
        return {"기간": f"{start_date} ~ {end_date}", "카테고리별_매출": []}
    breakdown = period_orders.groupby("category")["total_amount"].sum().sort_values(ascending=False)
    return {
        "기간": f"{start_date} ~ {end_date}",
        "카테고리별_매출": [{"카테고리": k, "매출": int(v)} for k, v in breakdown.items()],
    }


def tool_get_channel_breakdown(start_date: str, end_date: str) -> dict:
    """특정 기간의 유입 채널별 매출 순위"""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, _ = get_period_slices(users, orders, events, start, end)
    if period_orders.empty:
        return {"기간": f"{start_date} ~ {end_date}", "채널별_매출": []}
    merged = period_orders.merge(users[["user_id", "acquisition_channel"]], on="user_id")
    breakdown = merged.groupby("acquisition_channel")["total_amount"].sum().sort_values(ascending=False)
    return {
        "기간": f"{start_date} ~ {end_date}",
        "채널별_매출": [{"채널": CHANNEL_KR.get(k, k), "매출": int(v)} for k, v in breakdown.items()],
    }


def tool_get_segment_breakdown(start_date: str, end_date: str) -> dict:
    """특정 기간의 RFM 세그먼트(VIP/충성 고객/이탈 위험/휴면)별 매출. 이 세그먼트는 최근
    구매 행동(RFM) 기준의 상대적 등급이며, 아래 get_persona_counts의 고정 라벨과는 다른 기준이다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, _ = get_period_slices(users, orders, events, start, end)
    if period_orders.empty:
        return {"기간": f"{start_date} ~ {end_date}", "세그먼트별_매출": []}
    rfm = assign_segment(calculate_rfm(period_orders.copy()))
    breakdown = rfm.groupby("segment")["Monetary"].sum().sort_values(ascending=False)
    return {
        "기간": f"{start_date} ~ {end_date}",
        "세그먼트별_매출": [{"세그먼트": k, "매출": int(v)} for k, v in breakdown.items()],
    }


def tool_get_persona_counts() -> dict:
    """전체 고객을 페르소나(신규 탐색자/충동 구매자/할인 헌터/브랜드 충성 고객/이탈 위험 고객/
    휴면 고객)로 분류한 고객 수. 데이터 생성 시 고정된 라벨이라 기간과 무관하게 항상 같은 값이다."""
    users, _, _ = load_data()
    counts = users["persona_type"].value_counts()
    return {
        "전체_고객_수": len(users),
        "페르소나별_고객_수": [{"페르소나": PERSONA_KR.get(k, k), "고객수": int(v)} for k, v in counts.items()],
    }


def tool_get_cohort_retention(start_date: str, end_date: str) -> dict:
    """가입월 코호트별로 이후 몇 개월차에 재구매했는지 리텐션(%). 빈 값은 아직 그
    시점에 도달하지 않아 측정할 수 없는 것이지 재구매율이 0%라는 뜻이 아니다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    retention = compute_cohort_retention(users, orders, start, end)
    if retention.empty:
        return {"기간": f"{start_date} ~ {end_date}", "안내": "해당 기간에 가입한 코호트가 없거나 아직 측정 가능한 데이터가 없습니다."}
    rows = []
    for cohort, row in retention.iterrows():
        rows.append(
            {
                "가입월": str(cohort),
                "개월차별_재구매율": {
                    f"{m}개월차": (None if pd.isna(v) else round(float(v), 1)) for m, v in row.items()
                },
            }
        )
    return {"기간": f"{start_date} ~ {end_date}", "코호트_리텐션": rows}


def tool_get_top_priority_issue(start_date: str, end_date: str) -> dict:
    """지금 가장 시급하게 대응해야 할 고객 세그먼트를 규칙 기반으로 추천하고, 판단에 쓰인
    근거 지표(핵심 KPI, 페르소나 분포)를 함께 반환한다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    segment = recommend_segment(start, end, users, orders, events)
    return {
        "기간": f"{start_date} ~ {end_date}",
        "가장_시급한_세그먼트": segment,
        "참고_핵심지표": tool_get_kpi_summary(start_date, end_date),
        "참고_페르소나_분포": tool_get_persona_counts()["페르소나별_고객_수"],
    }


def tool_get_kpi_comparison(current_start: str, current_end: str, compare_start: str, compare_end: str) -> dict:
    """두 기간의 핵심 지표(GMV/AOV/주문건수/전환율/재구매율)를 각각 계산해서 나란히 비교하고
    증감률(delta_pct)까지 미리 계산해서 반환한다. '저번주보다', '지난달 대비 어때' 같은 비교
    질문에 사용한다. 모델이 직접 두 숫자를 빼거나 나누게 하지 않기 위해, 증감률까지 이
    함수가 계산해서 준다(다른 도구들과 같은 원칙: 숫자 계산은 항상 코드가 한다)."""
    current = tool_get_kpi_summary(current_start, current_end)
    compare = tool_get_kpi_summary(compare_start, compare_end)

    def _delta_pct(cur_val, prev_val):
        if not prev_val:
            return None
        return round((cur_val - prev_val) / prev_val * 100, 1)

    compare_keys = ["GMV", "AOV", "주문_건수", "활성_고객_수", "구매_전환율_퍼센트", "재구매율_퍼센트"]
    delta = {k: _delta_pct(current.get(k, 0), compare.get(k, 0)) for k in compare_keys}

    return {
        "현재_기간": current,
        "비교_기간": compare,
        "증감률_퍼센트(현재_기준_%p_아닌_증감률)": delta,
    }


def tool_get_gmv_trend(start_date: str, end_date: str) -> dict:
    """특정 기간을 월별로 나눠 GMV와 주문 수 추이를 반환한다. 'GMV 추이', '주문 수 추이',
    '요즘 매출 흐름이 어때' 같은 질문에 사용한다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, _ = get_period_slices(users, orders, events, start, end)
    if period_orders.empty:
        return {"기간": f"{start_date} ~ {end_date}", "월별_추이": []}
    monthly = period_orders.copy()
    monthly["월"] = monthly["order_date"].dt.to_period("M").astype(str)
    grouped = monthly.groupby("월").agg(GMV=("total_amount", "sum"), 주문_수=("order_id", "count")).reset_index()
    return {
        "기간": f"{start_date} ~ {end_date}",
        "월별_추이": [
            {"월": row["월"], "GMV": int(row["GMV"]), "주문_수": int(row["주문_수"])}
            for _, row in grouped.iterrows()
        ],
    }


def tool_get_purchase_funnel(start_date: str, end_date: str) -> dict:
    """특정 기간의 구매 퍼널(방문 → 상품조회 → 장바구니 → 구매) 단계별 이벤트 수와 단계 간
    전환율을 반환한다. '퍼널', '어디서 이탈이 많이 생겨' 같은 질문에 사용한다.

    대시보드 차트와 같은 기준(고유 방문자 수가 아니라 이벤트 발생 건수)으로 맞춰야
    챗봇 답변과 화면 숫자가 다르게 보이지 않는다 — 예: page_view 이벤트 총 건수를
    '방문'으로 센다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    _, period_events = get_period_slices(users, orders, events, start, end)

    steps = [("방문", "page_view"), ("상품조회", "product_view"), ("장바구니", "add_to_cart"), ("구매", "purchase")]

    funnel, prev_count = [], None
    for label, event_type in steps:
        count = int((period_events["event_type"] == event_type).sum())
        rate = round(count / prev_count * 100, 1) if prev_count else 100.0
        funnel.append({"단계": label, "이벤트_건수": count, "이전_단계_대비_전환율_퍼센트": rate})
        prev_count = count

    return {"기간": f"{start_date} ~ {end_date}", "구매_퍼널": funnel}


def tool_get_rfm_summary(start_date: str, end_date: str) -> dict:
    """특정 기간의 RFM(최근성/구매빈도/구매금액) 세그먼트별 평균값과 고객 수를 반환한다.
    'RFM 분포가 어때', '충성 고객은 보통 얼마나 자주 사' 같은 질문에 사용한다. (개별
    고객 산점도가 아니라 세그먼트별 요약 통계 — 도구는 근거로 인용 가능한 값만 준다.)"""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    period_orders, _ = get_period_slices(users, orders, events, start, end)
    if period_orders.empty:
        return {"기간": f"{start_date} ~ {end_date}", "세그먼트별_RFM": []}
    rfm = assign_segment(calculate_rfm(period_orders.copy()))
    grouped = rfm.groupby("segment").agg(
        고객수=("user_id", "count"),
        평균_최근성_일=("Recency", "mean"),
        평균_구매빈도=("Frequency", "mean"),
        평균_구매금액=("Monetary", "mean"),
    ).reset_index()
    return {
        "기간": f"{start_date} ~ {end_date}",
        "세그먼트별_RFM": [
            {
                "세그먼트": str(row["segment"]),
                "고객수": int(row["고객수"]),
                "평균_최근성_일": round(float(row["평균_최근성_일"]), 1),
                "평균_구매빈도": round(float(row["평균_구매빈도"]), 1),
                "평균_구매금액": int(row["평균_구매금액"]),
            }
            for _, row in grouped.iterrows()
        ],
    }


def tool_get_demographics(start_date: str, end_date: str) -> dict:
    """특정 기간에 활동한(방문/조회/구매 등 이벤트가 있었던) 고객의 성별·연령대 분포를
    반환한다. '고객 성별 비율', '연령대 분포가 어때' 같은 질문에 사용한다."""
    users, orders, events = load_data()
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    _, period_events = get_period_slices(users, orders, events, start, end)
    active_ids = period_events["user_id"].unique()
    active_users = users[users["user_id"].isin(active_ids)].copy()
    if active_users.empty:
        return {"기간": f"{start_date} ~ {end_date}", "성별_분포": [], "연령대_분포": []}

    gender_label = {"M": "남성", "F": "여성"}
    gender_counts = active_users["gender"].value_counts()
    gender_result = [{"성별": gender_label.get(k, k), "고객수": int(v)} for k, v in gender_counts.items()]

    def age_bucket(age):
        decade = int(age) // 10 * 10
        return "60대 이상" if decade >= 60 else f"{decade}대"

    active_users["연령대"] = active_users["age"].apply(age_bucket)
    age_counts = active_users["연령대"].value_counts()
    order = ["10대", "20대", "30대", "40대", "50대", "60대 이상"]
    age_result = [{"연령대": k, "고객수": int(age_counts.get(k, 0))} for k in order if k in age_counts.index]

    return {"기간": f"{start_date} ~ {end_date}", "성별_분포": gender_result, "연령대_분포": age_result}


def recommend_segment(start_date, end_date, users: pd.DataFrame, orders: pd.DataFrame, events: pd.DataFrame) -> str:
    """선택한 기간의 지표를 기준으로 탭3 PERSONAS 키 중 하나를 추천한다.

    이전 버전은 '휴면 고객 → 이탈 위험 고객 → ...' 순서로 조건을 하나씩 검사하다
    가장 먼저 걸리는 세그먼트를 그대로 리턴했다. 그런데 이 더미 데이터는
    휴면/이탈위험 페르소나가 항상 전체의 15% 이상을 차지하도록 설계돼 있어서,
    거의 모든 기간에서 첫 조건(휴면 고객)이 걸려버려 기간을 바꿔도 결과가
    항상 '휴면 고객'으로 고정되는 문제가 있었다.

    지금은 각 조건을 '기준치 대비 얼마나 심각한지'(값/기준치 비율)로 점수화한
    뒤 가장 점수가 높은 세그먼트를 고른다. 그리고 기간에 따라 값이 달라지는
    지표(장바구니 이탈률, 쿠폰 사용률, 재구매율, 신규 전환율 등)는 전체 기간이
    아니라 '선택한 기간' 데이터만으로 계산해서, 기간을 바꾸면 결과도 실제로
    달라지도록 했다.
    """
    orders = orders.copy()
    users = users.copy()
    events = events.copy()
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    users["signup_date"] = pd.to_datetime(users["signup_date"])
    events["timestamp"] = pd.to_datetime(events["timestamp"])

    period_orders, period_events = get_period_slices(users, orders, events, start_date, end_date)

    # get_dashboard_summary()의 total_users와는 다르게 '기간 내 신규가입자'가 아니라
    # '누적 전체 가입자'를 쓴다. dormant_count/at_risk_count는 가입 시점과 무관하게
    # 전체 고객 기준으로 세는 값이라, 분모도 같은 전체 고객 기준이어야 비율이 의미를
    # 가진다 — 기간 내 신규가입자 수로 나누면 분모가 지나치게 작아져서, 이 함수가
    # 원래 고치려던 문제("어떤 기간을 골라도 항상 휴면 고객만 1등으로 잡히는 문제")가
    # 다른 경로로 재발한다. (일부러 다르게 둔 것이며 실수가 아님)
    total_users = users[users["signup_date"].dt.date <= end_date]["user_id"].nunique()

    # 휴면/이탈위험 여부는 자체 계산(예: 며칠간 미구매) 대신 data/generate_data.py가
    # 애초에 지정해둔 persona_type 라벨을 그대로 쓴다. 대시보드 탭1의 "페르소나별
    # 고객 수" 차트와 항상 같은 숫자를 가리키게 하기 위해서다. 가입 시점에 고정되는
    # 라벨이라 이 두 값 자체는 기간을 바꿔도 변하지 않지만, 아래 다른 후보들(장바구니
    # 이탈률, 쿠폰 사용률, 신규 전환율 등)은 여전히 선택한 기간에 따라 달라지므로,
    # 그 기간에 더 심각한 다른 문제가 있으면 그쪽이 대신 선택될 수 있다.
    dormant_count = (users["persona_type"] == "dormant").sum()
    at_risk_count = (users["persona_type"] == "churn_risk").sum()

    # 신규 가입자(기간 종료일 기준 30일 이내) 중 해당 기간 내 구매 전환율
    new_users = users[(pd.Timestamp(end_date) - users["signup_date"]).dt.days.between(0, 30)]
    new_user_ids = set(new_users["user_id"])
    new_user_purchasers = period_orders[period_orders["user_id"].isin(new_user_ids)]["user_id"].nunique()
    new_user_purchase_rate = (new_user_purchasers / len(new_users)) if len(new_users) > 0 else 1.0

    # 장바구니 이탈률 (선택 기간 내)
    cart_users = period_events[period_events["event_type"] == "add_to_cart"]["user_id"].nunique()
    purchase_users = period_events[period_events["event_type"] == "purchase"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    # 쿠폰 사용률 (선택 기간 내)
    coupon_rate = period_orders["coupon_used"].mean() * 100 if ("coupon_used" in period_orders.columns and len(period_orders)) else 0

    # 재구매율 (선택 기간 내)
    repeat_purchase_rate = compute_repeat_purchase_rate(period_orders)

    # --- 조건별 '기준치 대비 심각도' 점수화 (1.0 이상이면 기준치 초과) ---
    # 휴면/이탈위험은 이제 둘 다 persona_type 기준 고정 인원(각 150명, 전체의 15%)이라
    # divisor를 서로 다르게 두면(예전 0.15 vs 0.10) 둘 중 하나가 기간과 무관하게 거의
    # 항상 이겨버린다 — 두 divisor를 0.15로 맞춰서, 이 둘이 같은 조건이면 동점이 되고
    # 실제로 그 기간에 더 심각한 다른 지표(카트 이탈률 등)가 있으면 그쪽이 이길 수 있게 했다.
    candidates = [
        ("휴면 고객", (dormant_count / total_users) / 0.15 if total_users else 0),
        ("이탈 위험 고객", (at_risk_count / total_users) / 0.15 if total_users else 0),
        ("신규 탐색자", max(0.0, (0.3 - new_user_purchase_rate) / 0.3) if len(new_users) > 0 else 0.0),
        ("이탈 위험 고객", (cart_abandon_rate / 100) / 0.45),
        ("할인 구매자", (coupon_rate / 100) / 0.50),
        ("브랜드 충성 고객", (repeat_purchase_rate / 100) / 0.70),
        ("충동 구매자", 0.5),  # 다른 이슈가 뚜렷하지 않을 때를 위한 기본값
    ]

    best_segment, _ = max(candidates, key=lambda pair: pair[1])
    return best_segment


# ---------------------------------------------------------
# 챗봇 엔진 — Claude에게 도구 목록을 주고, 도구 호출을 우리 코드가 직접
# 실행해서 그 결과만 다시 넘겨주는 방식(tool use)으로 숫자를 다룬다.
# 모델이 숫자를 스스로 계산/추정하지 못하게 막는 게 이 구조의 핵심이다.
# ---------------------------------------------------------

CHATBOT_TOOLS = [
    {
        "name": "get_kpi_summary",
        "description": "특정 기간의 GMV, AOV, 주문 건수, 활성 고객 수, 구매 전환율, 장바구니 이탈률, 재구매율을 정확히 계산해서 반환합니다. 매출/전환율/주문 관련 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_category_breakdown",
        "description": "특정 기간의 카테고리별 매출 순위를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_channel_breakdown",
        "description": "특정 기간의 유입 채널(SNS/검색광고/직접유입/이메일/추천)별 매출 순위를 반환합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_segment_breakdown",
        "description": "특정 기간의 RFM 세그먼트(VIP/충성 고객/이탈 위험/휴면)별 매출을 반환합니다. 최근 구매 행동 기준의 상대적 등급입니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_persona_counts",
        "description": "전체 고객을 페르소나(신규 탐색자/충동 구매자/할인 헌터/브랜드 충성 고객/이탈 위험 고객/휴면 고객)로 분류한 고객 수를 반환합니다. 기간과 무관하게 고정된 값입니다.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_cohort_retention",
        "description": "가입월 코호트별로 이후 몇 개월차에 재구매했는지 리텐션(%)을 반환합니다. '재구매 유지율', '코호트' 관련 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_top_priority_issue",
        "description": "지금 가장 시급하게 대응해야 할 고객 세그먼트를 규칙 기반으로 추천합니다. '가장 시급한 문제', '지금 뭐가 문제야' 같은 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_kpi_comparison",
        "description": "두 기간의 핵심 지표(GMV/AOV/주문건수/전환율/재구매율)를 비교하고 증감률까지 계산해서 반환합니다. '저번주보다', '지난달 대비 어때' 같은 비교 질문에는 get_kpi_summary를 두 번 부르지 말고 반드시 이 도구를 쓰세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "current_start": {"type": "string", "description": "비교 기준(현재) 기간 시작일 (YYYY-MM-DD)"},
                "current_end": {"type": "string", "description": "비교 기준(현재) 기간 종료일 (YYYY-MM-DD)"},
                "compare_start": {"type": "string", "description": "비교 대상(과거) 기간 시작일 (YYYY-MM-DD)"},
                "compare_end": {"type": "string", "description": "비교 대상(과거) 기간 종료일 (YYYY-MM-DD)"},
            },
            "required": ["current_start", "current_end", "compare_start", "compare_end"],
        },
    },
    {
        "name": "get_gmv_trend",
        "description": "특정 기간을 월별로 나눠 GMV와 주문 수 추이를 반환합니다. 'GMV 추이', '주문 수 추이' 같은 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_purchase_funnel",
        "description": "특정 기간의 구매 퍼널(방문 → 상품조회 → 장바구니 → 구매) 단계별 방문자 수와 단계 간 전환율을 반환합니다. '퍼널', '어디서 이탈이 많이 생겨' 같은 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_rfm_summary",
        "description": "특정 기간의 RFM(최근성/구매빈도/구매금액) 세그먼트별 평균값과 고객 수를 반환합니다. 'RFM 분포', '충성 고객은 얼마나 자주 사' 같은 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "get_demographics",
        "description": "특정 기간에 활동한 고객의 성별·연령대 분포를 반환합니다. '고객 성별 비율', '연령대 분포' 같은 질문에 사용하세요.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "조회 시작일 (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "조회 종료일 (YYYY-MM-DD)"},
            },
            "required": ["start_date", "end_date"],
        },
    },
]

TOOL_FUNCTIONS = {
    "get_kpi_summary": tool_get_kpi_summary,
    "get_category_breakdown": tool_get_category_breakdown,
    "get_channel_breakdown": tool_get_channel_breakdown,
    "get_segment_breakdown": tool_get_segment_breakdown,
    "get_persona_counts": tool_get_persona_counts,
    "get_cohort_retention": tool_get_cohort_retention,
    "get_top_priority_issue": tool_get_top_priority_issue,
    "get_kpi_comparison": tool_get_kpi_comparison,
    "get_gmv_trend": tool_get_gmv_trend,
    "get_purchase_funnel": tool_get_purchase_funnel,
    "get_rfm_summary": tool_get_rfm_summary,
    "get_demographics": tool_get_demographics,
}

# 답변에 "출처 태그"/"생각 과정"을 보여줄 때 쓰는 사람이 읽기 좋은 라벨.
# chart_key는 나중에 대시보드 쪽에서 같은 이름의 차트로 이동/강조하는 데 쓸 수 있도록
# 붙여둔 것 — 아직 대시보드 쪽(dashboard/charts.py)에 대응 코드가 없으면 그냥 무시된다.
TOOL_LABELS = {
    "get_kpi_summary": {"label": "핵심 지표(GMV·AOV·전환율 등) 조회", "chart_key": "gmv"},
    "get_kpi_comparison": {"label": "기간별 지표 비교", "chart_key": "gmv"},
    "get_category_breakdown": {"label": "카테고리별 매출 조회", "chart_key": "category"},
    "get_channel_breakdown": {"label": "유입 채널별 매출 조회", "chart_key": "channel"},
    "get_segment_breakdown": {"label": "RFM 세그먼트별 매출 조회", "chart_key": "segment"},
    "get_persona_counts": {"label": "페르소나별 고객 수 조회", "chart_key": "persona"},
    "get_cohort_retention": {"label": "코호트 리텐션 조회", "chart_key": "cohort"},
    "get_top_priority_issue": {"label": "시급한 세그먼트 진단", "chart_key": None},
    "get_gmv_trend": {"label": "GMV·주문 수 추이 조회", "chart_key": "gmv_trend"},
    "get_purchase_funnel": {"label": "구매 퍼널 조회", "chart_key": "funnel"},
    "get_rfm_summary": {"label": "RFM 세그먼트별 요약 조회", "chart_key": "rfm"},
    "get_demographics": {"label": "성별·연령대 분포 조회", "chart_key": "demographics"},
}


def _describe_tool_call(name: str, tool_input: dict) -> str:
    """도구 호출 1건을 사람이 읽을 수 있는 한 줄로 요약한다 (생각 과정 / 출처 표시용)."""
    info = TOOL_LABELS.get(name, {"label": name})
    label = info["label"]
    if "current_start" in tool_input:
        period = f"{tool_input.get('current_start', '')}~{tool_input.get('current_end', '')} vs {tool_input.get('compare_start', '')}~{tool_input.get('compare_end', '')}"
    elif "start_date" in tool_input:
        period = f"{tool_input.get('start_date', '')} ~ {tool_input.get('end_date', '')}"
    else:
        period = ""
    return f"{label}" + (f" ({period})" if period else "")

CHATBOT_SYSTEM_PROMPT = f"""당신은 ATHLEPA CRM 대시보드에 내장된 데이터 조회 챗봇입니다.

우리가 가진 데이터는 {DATASET_MIN_DATE} ~ {DATASET_MAX_DATE} 기간의 시뮬레이션 데이터입니다.
사용자가 "이번 주", "지난달", "최근" 같은 상대적 표현을 쓰면, 오늘 날짜가 아니라 이
데이터의 마지막 날짜({DATASET_MAX_DATE})를 기준으로 계산하세요.

질문은 아래 네 종류로 나뉩니다. 반드시 이 방식을 지키세요:

1. 사실 조회형 (매출, 전환율, 세그먼트, 리텐션 등 단순 수치 확인 질문)
   → 반드시 제공된 도구(tool)를 호출해서 실제 계산된 값을 받아온 뒤, 그 값만 근거로
   간결하게 답하세요. 도구를 쓰지 않고 스스로 숫자를 추정하거나 계산하지 마세요. 도구가
   반환한 숫자를 다시 나누거나 곱해서 새로운 비율을 스스로 만들어내지도 마세요.
   "저번주보다", "지난달 대비" 처럼 두 기간을 비교하는 질문이면 get_kpi_summary를
   두 번 부르지 말고 반드시 get_kpi_comparison을 사용해서 증감률까지 받아오세요.

2. 진단/분석형 (예: "왜 그래?", "무슨 문제야?", "지금 가장 시급한 문제는?")
   → 단순 수치가 아니라 원인과 다음 행동까지 묻는 질문입니다. 반드시 아래 4단계 구조를
   그대로 사용해서 답하세요 (각 단계를 굵게 표시된 소제목으로 구분):
   **결과:** 무엇이 어떻게 됐는지 핵심 수치로 1문장.
   **원인:** 그 결과가 왜 나왔는지, 도구가 반환한 값(예: 페르소나 분포, 채널별 매출 등)에
     근거해서 1~2문장. 도구 결과로 확인되지 않는 원인은 추측이라는 걸 밝히세요.
   **근거:** 이 진단에 사용한 지표 이름·기간·계산 기준을 짧게 명시하세요 (예: "2026-06-01~
     2026-06-07 기간의 RFM 세그먼트별 매출 기준"). 사용자가 원본 데이터를 확인하고
     싶어할 수 있다는 걸 염두에 두고 구체적으로 쓰세요.
   **추천 액션:** 다음에 뭘 하면 좋을지 1문장. 이 액션은 검증된 사실이 아니라 제안이라는
     점을 문장 안에서 자연스럽게 드러내세요.

3. 전략/의견형 (예: "전환율을 어떻게 올려야 할까?", "뭐가 더 필요할까?")
   → 도구만으로는 답할 수 없는 질문입니다. 답변은 하되, 답변 맨 앞에 반드시
   ":material/lightbulb: **AI 의견** (검증된 사실이 아닌 참고용 제안입니다)"라는 문구를 그대로 넣고
   시작하세요. 가능하면 관련 도구를 먼저 호출해서 실제 지표를 근거로 함께 제시하세요.

4. 무관한 질문 (매장/CRM 데이터와 전혀 관계없는 질문, 예: 날씨, 잡담, 이 챗봇의 정체 등)
   → 도구를 호출하지 말고, "죄송해요, 이 챗봇은 ATHLEPA 매장 데이터 관련 질문만 답할
   수 있어요."라고 정중히 안내하세요.

규칙:
- 답변은 한국어 존댓말(합니다체)로, 2~4문장 정도로 간결하게 작성하세요.
- 핵심 수치는 **마크다운 볼드체**로 강조하세요.
- 도구 호출 결과에 없는 정보는 추측하지 마세요.
- 사용자가 물어본 지표만 답하세요. 도구는 여러 지표를 한 번에 묶어서 반환하지만,
  사용자가 요청하지 않은 지표(예: "매출 얼마야?"라고 물었는데 함께 돌아온 전환율/
  이탈률/재구매율 같은 것)는 먼저 나서서 언급하지 마세요. 예를 들어 "이번 주 매출은?"
  이라는 질문에는 GMV 한 가지만 답하고, 주문 건수·AOV·전환율 등은 꺼내지 마세요.
  "전체적으로 어때?", "요약해줘"처럼 폭넓게 물어볼 때만 여러 지표를 한 번에 정리하세요.
- 새 질문에 기간이 명시돼 있지 않으면, 앞선 대화에서 썼던 기간을 조용히 이어받지
  마세요. 기간을 특정하지 않은 질문은 전체 기간({DATASET_MIN_DATE} ~ {DATASET_MAX_DATE})을
  기본값으로 쓰세요. "같은 기간으로", "그때 기준" 처럼 사용자가 이전 기간을 이어가겠다고
  명시적으로 표현했을 때만 앞선 질문의 기간을 그대로 사용하세요.
"""


def run_chatbot_turn(messages: list) -> tuple[str, list]:
    """messages: [{"role": "user"/"assistant", "content": "..."}] 형태의 대화 이력을 받아
    필요하면 도구를 호출해가며 (최종 답변 텍스트, 실제로 호출한 도구 목록)을 반환한다.

    도구 목록은 [{"name": ..., "input": {...}}, ...] 형태로, "출처 태그"·"생각 과정"
    UI를 그리는 데 쓰인다. Claude의 진짜 내부 추론(hidden reasoning)이 아니라, 실제로
    조회한 데이터 도구를 그대로 보여주는 것 — 근거를 투명하게 보여준다는 목적에는 오히려
    이쪽이 더 정확하다(모델이 지어낸 것처럼 보이는 가짜 사고 과정을 만들지 않는다)."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    working_messages = list(messages)
    tool_calls = []

    for _ in range(4):  # 도구를 여러 번 호출하는 경우를 대비한 안전장치(무한루프 방지)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=CHATBOT_SYSTEM_PROMPT,
            tools=CHATBOT_TOOLS,
            messages=working_messages,
        )

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            return text, tool_calls

        working_messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            func = TOOL_FUNCTIONS.get(block.name)
            try:
                result = func(**block.input) if func else {"오류": f"알 수 없는 도구: {block.name}"}
            except Exception as e:
                result = {"오류": str(e)}
            tool_calls.append({"name": block.name, "input": block.input})
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
        working_messages.append({"role": "user", "content": tool_results})

    return "죄송해요, 답변을 만드는 데 문제가 생겼어요. 다시 시도해주세요.", tool_calls


# ---------------------------------------------------------
# 메인 패널 (AI 챗봇)
# ---------------------------------------------------------

EXAMPLE_QUESTIONS = [
    "지금 가장 시급한 문제는?",
    "이번 주 매출은 얼마야?",
    "저번 주보다 매출 늘었어?",
    "카테고리별 매출 순위 알려줘",
]

EXECUTION_MODES = ["제안만 (승인 후 실행)", "완전 자동 실행"]

# 플로팅 챗봇(FAB + 패널)을 CSS만으로 화면에 "떠 있게" 만드는 트릭.
# st.container(key="...")가 만드는 div에 실제 클래스 이름(st-key-<key>)이 그대로 붙는다는
# 걸 이용해서, 그 div에 position:fixed를 강제로 씌운다. Streamlit 버전이 바뀌면 이 클래스
# 구조가 달라져서 깨질 수 있는데, 그럴 땐 브라우저 개발자 도구로 실제 클래스명을 다시
# 확인해서 여기 선택자만 맞춰주면 된다.
AI_CHAT_CSS = """
<style>
div[class*="st-key-ai-fab"] {
    position: fixed; right: 28px; bottom: 28px; z-index: 999999; width: auto;
}
div[class*="st-key-ai-fab"] button {
    border-radius: 999px; background: var(--athlepa-primary); color: #fff; border: none;
    box-shadow: 0 6px 18px rgba(124,58,237,0.35); font-weight: 600; padding: 10px 18px;
}
div[class*="st-key-ai-fab"] button:hover { background: var(--athlepa-primary-hover); color: #fff; }

/* 패널이 열려 있을 때, 화면 전체를 덮는 투명 버튼(빈 공간 클릭 시 닫기용).
   패널(z-index 더 높음)이 그 위를 덮고 있어서, 패널 '바깥'을 눌렀을 때만 이 버튼이 눌린다. */
div[class*="st-key-ai-overlay"] { position: fixed; inset: 0; z-index: 999997; }
div[class*="st-key-ai-overlay"] button {
    width: 100vw; height: 100vh; background: transparent; border: none; cursor: default;
}

div[class*="st-key-ai-panel"] {
    position: fixed; right: 28px; bottom: 96px; width: 380px; max-height: 72vh;
    background: #fff; border: 1px solid var(--athlepa-border); border-radius: 14px;
    box-shadow: 0 16px 40px rgba(17,16,24,0.18); z-index: 999998;
    overflow-y: auto; padding: 8px 14px 12px;
}

div[class*="st-key-ai-src-"] button {
    font-size: 10.5px !important; padding: 2px 10px !important; height: auto !important;
    min-height: 0 !important; border-radius: 999px !important;
    border: 1px solid var(--athlepa-border) !important; background: #fff !important;
    color: var(--athlepa-primary-hover) !important; margin: 0 4px 4px 0 !important;
}
div[class*="st-key-ai-src-"] button:hover:not(:disabled) {
    border-color: var(--athlepa-primary) !important; background: var(--athlepa-secondary) !important;
}

div[class*="st-key-ai-fb-"] button {
    padding: 2px 6px !important; font-size: 12px !important; height: auto !important;
    min-height: 0 !important; border: none !important; opacity: 0.55;
}
div[class*="st-key-ai-fb-"] button:hover { opacity: 1; }

div[class*="st-key-ai_chat_form"] button {
    white-space: nowrap; padding-left: 10px !important; padding-right: 10px !important;
}
</style>
"""


def render_chat_text(text: str):
    """챗봇 메시지를 렌더링한다. 표/목록 같은 실제 마크다운 문법을 살리기 위해
    Streamlit 기본 마크다운 파서(st.markdown)를 그대로 쓴다."""
    st.markdown(text)


def _init_chat_state():
    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("chat_meta", [])
    st.session_state.setdefault("chat_panel_open", False)
    st.session_state.setdefault("chat_seen_count", 0)
    st.session_state.setdefault("ai_execution_mode", EXECUTION_MODES[0])


def _ask_chatbot(question: str):
    """사용자 질문 1건을 대화 이력에 추가하고, 챗봇 답변까지 받아서 메타데이터(출처/생각
    과정)와 함께 기록한다.

    chat_messages는 Claude API에 그대로 넘어가는 형식(role/content만 있어야 함)이라, 화면
    표시용으로 추가한 정보(출처, 생각 과정, 피드백)는 섞지 않고 별도 리스트(chat_meta)에
    같은 순서로 나란히 쌓는다."""
    st.session_state.chat_messages.append({"role": "user", "content": question})
    st.session_state.chat_meta.append({})

    with st.spinner("데이터 확인 중..."):
        try:
            answer, tool_calls = run_chatbot_turn(st.session_state.chat_messages)
        except Exception as e:
            answer, tool_calls = f"죄송해요, 답변 중 오류가 발생했어요 ({e}).", []

    thinking, sources, seen = [], [], set()
    for call in tool_calls:
        desc = _describe_tool_call(call["name"], call["input"])
        thinking.append(desc)
        chart_key = TOOL_LABELS.get(call["name"], {}).get("chart_key")
        dedupe_key = (call["name"], chart_key)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            sources.append({"label": desc, "chart_key": chart_key})

    st.session_state.chat_messages.append({"role": "assistant", "content": answer})
    st.session_state.chat_meta.append({"sources": sources, "thinking": thinking, "feedback": None})


def _jump_to_chart(chart_key: str):
    """출처 태그를 클릭하면 대시보드 페이지로 이동하면서, 어떤 차트를 보고 있었는지
    session_state에 남겨둔다. dashboard/charts.py 쪽에서 이 값(dashboard_highlight_chart)을
    읽어서 해당 차트를 강조 표시해주는 코드는 아직 없다 — 그건 그 파일 담당자와 별도로
    맞춰야 한다. 지금은 페이지 이동까지만 이 파일 안에서 완결된다."""
    st.session_state["dashboard_highlight_chart"] = chart_key
    st.switch_page("dashboard/page.py")


def _render_feedback(idx: int, meta: dict):
    """메시지 품질 확인용 👍/👎. 지금은 화면에만 표시되고 파일로 기록되지는 않는다."""
    with st.container(key=f"ai-fb-{idx}"):
        c1, c2, _ = st.columns([1, 1, 10])
        with c1:
            up_type = "primary" if meta.get("feedback") == "up" else "secondary"
            if st.button("👍", key=f"fb_up_{idx}", type=up_type):
                meta["feedback"] = None if meta.get("feedback") == "up" else "up"
                st.rerun()
        with c2:
            down_type = "primary" if meta.get("feedback") == "down" else "secondary"
            if st.button("👎", key=f"fb_down_{idx}", type=down_type):
                meta["feedback"] = None if meta.get("feedback") == "down" else "down"
                st.rerun()


def _render_message(idx: int, msg: dict, meta: dict):
    with st.chat_message(msg["role"]):
        if msg["role"] != "assistant":
            st.markdown(msg["content"])
            return

        thinking = meta.get("thinking") or []
        if thinking:
            with st.expander("🔍 생각 과정 보기", expanded=False):
                for step in thinking:
                    st.caption(f"· {step}")

        render_chat_text(msg["content"])

        sources = meta.get("sources") or []
        if sources:
            with st.container(key=f"ai-src-{idx}"):
                for s_i, source in enumerate(sources):
                    if source["chart_key"]:
                        if st.button(f"📊 출처: {source['label']}", key=f"src_{idx}_{s_i}"):
                            _jump_to_chart(source["chart_key"])
                    else:
                        st.button(f"출처: {source['label']}", key=f"src_{idx}_{s_i}", disabled=True)

        _render_feedback(idx, meta)


def ask_chatbot(question: str):
    """다른 페이지(예: dashboard/charts.py)에서 차트나 지표 카드를 클릭했을 때 호출하는
    함수. 챗봇 패널이 자동으로 열리고, 그 질문을 바로 물어본 것처럼 답변까지 나온다.

    사용법: 클릭 이벤트가 일어나는 곳(예: st.button 안)에서
        from ai_insights.chatbot import ask_chatbot
        ask_chatbot("세그먼트별 매출을 분석해줘")
    이렇게 딱 한 줄만 호출하면 된다 — 별도로 rerun을 부르거나 패널을 열 필요 없음.
    (내부적으로 예시 질문 버튼과 똑같은 방식(session_state의 pending_question)을 쓰기
    때문에, 그 값이 세팅된 채로 다음에 render_floating_chat()이 그려질 때 자동으로
    처리된다 — Home.py는 pg.run() 다음에 항상 render_floating_chat()을 부르므로,
    같은 스크립트 실행 안에서 바로 반영된다.)"""
    st.session_state["pending_question"] = question


def render_status_badge():
    """'AI 어시스턴트 온라인' 상태 배지 - 페이지 제목 옆에 나란히 배치."""
    st.markdown(
        "<div style='text-align:right;margin-top:14px;font-size:11px;color:var(--athlepa-muted-text)'>"
        "<span style='display:inline-block;width:6px;height:6px;border-radius:50%;"
        "background:#34D399;margin-right:5px'></span>AI 어시스턴트 온라인</div>",
        unsafe_allow_html=True,
    )


def render_floating_chat(force_open_once: bool = False):
    """모든 페이지 맨 아래에서 호출하는 플로팅 챗봇(FAB + 패널). ATHLEPA CRM 어디서든
    같은 대화(session_state 공유)를 이어갈 수 있다.

    force_open_once=True로 부르면('고객 분석' 페이지에서 사용) 이번 세션에서 아직 한 번도
    자동으로 열어준 적이 없을 때만 패널을 미리 열어둔다. 사용자가 그 뒤 직접 닫으면
    다시 강제로 열지 않는다."""
    _init_chat_state()
    st.markdown(AI_CHAT_CSS, unsafe_allow_html=True)

    if force_open_once and not st.session_state.get("chat_auto_opened_once"):
        st.session_state["chat_panel_open"] = True
        st.session_state["chat_auto_opened_once"] = True

    # 예시 질문 클릭/입력창 전송을 그 자리에서 바로 처리하면, "예시 질문을 숨겨야 하는지"
    # 판단이 이미 끝난 뒤에 메시지가 추가되는 꼴이라 로딩 스피너가 도는 동안 예시 질문이
    # 잠깐 같이 보인다. 그래서 클릭 즉시 처리하지 않고 "다음 질문" 표시만 남겨 rerun하고,
    # 그 rerun에서 처리한다.
    pending_question = st.session_state.pop("pending_question", None)
    if pending_question:
        st.session_state["chat_panel_open"] = True

    is_open = st.session_state["chat_panel_open"]

    if is_open:
        with st.container(key="ai-overlay"):
            if st.button("", key="ai_overlay_btn"):
                st.session_state["chat_panel_open"] = False
                st.rerun()

        with st.container(key="ai-panel"):
            head_l, head_r = st.columns([5, 1])
            with head_l:
                st.markdown("**🤖 AI 어시스턴트**")
            with head_r:
                if st.button("✕", key="ai_panel_close"):
                    st.session_state["chat_panel_open"] = False
                    st.rerun()

            with st.expander("⚙️ AI 실행 권한", expanded=False):
                current_mode = st.session_state["ai_execution_mode"]
                chosen_mode = st.radio(
                    "AI가 액션(캠페인 생성 등)을 제안했을 때",
                    EXECUTION_MODES,
                    index=EXECUTION_MODES.index(current_mode),
                    key="ai_execution_mode_radio",
                )
                st.session_state["ai_execution_mode"] = chosen_mode
                st.caption(
                    "지금은 챗봇이 데이터를 '조회'만 하고 있어서 실제로 실행되는 액션은 "
                    "아직 없어요. 나중에 자동화 기능이 연결되면 이 설정을 따르게 됩니다."
                )

            if not st.session_state.chat_messages:
                st.markdown(
                    "안녕하세요! ATHLEPA CRM AI 어시스턴트입니다.  \n"
                    "고객 데이터 분석, 세그먼트 조회 등 원하는 걸 물어보세요."
                )
                cols = st.columns(2)
                for i, q in enumerate(EXAMPLE_QUESTIONS):
                    with cols[i % 2]:
                        if st.button(q, key=f"example_q_{i}", use_container_width=True):
                            pending_question = q

            for idx, (msg, meta) in enumerate(zip(st.session_state.chat_messages, st.session_state.chat_meta)):
                _render_message(idx, msg, meta)

            if pending_question:
                _ask_chatbot(pending_question)
                st.rerun()

            with st.form(key="ai_chat_form", clear_on_submit=True, border=False):
                c1, c2 = st.columns([4, 1.3])
                with c1:
                    user_input = st.text_input(
                        "질문", key="ai_chat_input_box", label_visibility="collapsed",
                        placeholder="궁금한 걸 물어보세요",
                    )
                with c2:
                    submitted = st.form_submit_button("보내기", use_container_width=True)
            if submitted and user_input.strip():
                st.session_state["pending_question"] = user_input.strip()
                st.rerun()

        st.session_state["chat_seen_count"] = len(st.session_state.chat_messages)

    with st.container(key="ai-fab"):
        unseen = len(st.session_state.chat_messages) - st.session_state["chat_seen_count"]
        if is_open or unseen <= 0:
            fab_label = "💬 AI 어시스턴트"
        else:
            fab_label = f"💬 AI 어시스턴트 · {unseen}"
        if st.button(fab_label, key="ai_fab_btn"):
            st.session_state["chat_panel_open"] = not is_open
            st.rerun()
