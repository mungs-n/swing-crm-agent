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


def _annotate_rank_deviation(items: list, value_key: str) -> list:
    """순위형 목록(카테고리별/채널별/세그먼트별 매출, 페르소나별 고객 수 등)에 '평균 대비
    배율', '1위와의 격차(%)'를 코드가 미리 계산해서 붙여준다.

    LLM한테 원본 숫자만 던져주고 "이걸로 분석해줘"라고 맡기면, 뭐가 특이한 포인트인지
    스스로 판단해야 해서 안전하고 뭉뚱그린 일반론으로 답하는 경향이 있다. 대신 "뭐가
    특이한지"는 여기서 코드가 미리 찾아서 수치로 넘겨주고, LLM한테는 "이 수치를 자연어로
    설명만 해"라고 역할을 좁혀주면 훨씬 구체적인 답이 나온다."""
    if not items:
        return items
    values = [it[value_key] for it in items]
    avg = sum(values) / len(values)
    top = values[0]
    for it in items:
        it["평균_대비_배율"] = round(it[value_key] / avg, 2) if avg else None
        it["1위와의_격차_퍼센트"] = round((top - it[value_key]) / top * 100, 1) if top else 0.0
    return items


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
    items = [{"카테고리": k, "매출": int(v)} for k, v in breakdown.items()]
    return {
        "기간": f"{start_date} ~ {end_date}",
        "카테고리별_매출": _annotate_rank_deviation(items, "매출"),
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
    items = [{"채널": CHANNEL_KR.get(k, k), "매출": int(v)} for k, v in breakdown.items()]
    return {
        "기간": f"{start_date} ~ {end_date}",
        "채널별_매출": _annotate_rank_deviation(items, "매출"),
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
    items = [{"세그먼트": k, "매출": int(v)} for k, v in breakdown.items()]
    return {
        "기간": f"{start_date} ~ {end_date}",
        "세그먼트별_매출": _annotate_rank_deviation(items, "매출"),
    }


def tool_get_persona_counts() -> dict:
    """전체 고객을 페르소나(신규 탐색자/충동 구매자/할인 헌터/브랜드 충성 고객/이탈 위험 고객/
    휴면 고객)로 분류한 고객 수. 데이터 생성 시 고정된 라벨이라 기간과 무관하게 항상 같은 값이다."""
    users, _, _ = load_data()
    counts = users["persona_type"].value_counts()
    items = [{"페르소나": PERSONA_KR.get(k, k), "고객수": int(v)} for k, v in counts.items()]
    return {
        "전체_고객_수": len(users),
        "페르소나별_고객_수": _annotate_rank_deviation(items, "고객수"),
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
    rows, prev_gmv = [], None
    for _, row in grouped.iterrows():
        gmv = int(row["GMV"])
        # 전월 대비 증감률도 여기서 미리 계산해서 준다 — LLM이 "추세가 어때?" 질문에
        # 매번 자기가 앞뒤 달 숫자를 빼고 나누게 하지 않기 위해서다(다른 도구들과 같은
        # 원칙: 숫자 계산은 항상 코드가 한다).
        delta = round((gmv - prev_gmv) / prev_gmv * 100, 1) if prev_gmv else None
        rows.append({"월": row["월"], "GMV": gmv, "주문_수": int(row["주문_수"]), "전월_대비_증감률_퍼센트": delta})
        prev_gmv = gmv
    return {"기간": f"{start_date} ~ {end_date}", "월별_추이": rows}


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
    items = [
        {
            "세그먼트": str(row["segment"]),
            "고객수": int(row["고객수"]),
            "평균_최근성_일": round(float(row["평균_최근성_일"]), 1),
            "평균_구매빈도": round(float(row["평균_구매빈도"]), 1),
            "평균_구매금액": int(row["평균_구매금액"]),
        }
        for _, row in grouped.iterrows()
    ]
    # groupby 결과는 세그먼트 이름 순서로 나오지, 매출 큰 순서가 아니다.
    # _annotate_rank_deviation은 items[0]을 "1위"로 취급하므로, 반드시 먼저 정렬해야 한다.
    items.sort(key=lambda r: r["평균_구매금액"], reverse=True)
    return {
        "기간": f"{start_date} ~ {end_date}",
        "세그먼트별_RFM": _annotate_rank_deviation(items, "평균_구매금액"),
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
    age_result.sort(key=lambda r: r["고객수"], reverse=True)  # _annotate_rank_deviation은 1위가 맨 앞이라고 가정

    return {
        "기간": f"{start_date} ~ {end_date}",
        "성별_분포": _annotate_rank_deviation(gender_result, "고객수"),
        "연령대_분포": _annotate_rank_deviation(age_result, "고객수"),
    }


CAMPAIGN_CHANNELS = ["카카오톡", "SMS", "이메일", "웹푸시"]


def tool_propose_campaign(segment: str, channel: str, message: str) -> dict:
    """캠페인 제안 카드를 만든다. 대상 인원수는 모델이 지어내지 않고 항상
    get_persona_counts와 같은 실제 데이터에서 가져온다 — message(캠페인 문구)만 모델이
    직접 작성한 창작 콘텐츠이고, 숫자(대상 인원)는 이 함수가 코드로 채운다."""
    counts = tool_get_persona_counts()["페르소나별_고객_수"]
    audience = next((c["고객수"] for c in counts if c["페르소나"] == segment), None)
    return {
        "세그먼트": segment,
        "대상_인원": audience,
        "채널": channel if channel in CAMPAIGN_CHANNELS else CAMPAIGN_CHANNELS[0],
        "메시지": message,
        "안내": (
            "이 인원수는 실제 페르소나 분류 기준이며, 실행 전 화면에서 문구를 수정할 수 있습니다."
            if audience is not None
            else "일치하는 페르소나를 찾지 못해 대상 인원을 확인할 수 없습니다 — 정확한 페르소나 이름으로 다시 요청해주세요."
        ),
    }


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
    {
        "name": "propose_campaign",
        "description": "캠페인 제안 카드를 만듭니다. '~한테 캠페인 만들어줘', '~세그먼트한테 메시지 보내줘' 같은 요청에 사용하세요. 화면에 제안 카드(수정 가능한 문구 + 실행 버튼)가 표시되고, 실제 기록은 사용자가 버튼을 눌러야(또는 완전 자동 모드면 곧바로) 남습니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "segment": {
                    "type": "string",
                    "description": "대상 페르소나. 반드시 다음 중 하나 그대로: 신규 탐색자, 충동 구매자, 할인 헌터, 브랜드 충성 고객, 이탈 위험 고객, 휴면 고객",
                },
                "channel": {
                    "type": "string",
                    "description": "발송 채널. 다음 중 하나: 카카오톡, SMS, 이메일, 웹푸시. 사용자가 명시하지 않으면 카카오톡을 기본값으로 쓰세요.",
                },
                "message": {
                    "type": "string",
                    "description": "그 세그먼트 특성에 맞게 직접 작성한 짧고 매력적인 캠페인 메시지 문구 (이 도구는 문구를 대신 써주지 않습니다 — 모델이 직접 작성해서 넣어야 합니다)",
                },
            },
            "required": ["segment", "channel", "message"],
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
    "propose_campaign": tool_propose_campaign,
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
    "propose_campaign": {"label": "캠페인 제안 생성", "chart_key": "persona"},
}


# 답변에 쓰인 차트(chart_key)에 따라 자연스럽게 이어질 만한 후속 질문 후보.
# 프로토타입의 "차트별 퀵리플라이"를 재현한 것 — 어떤 차트를 클릭해서 나온 답변이든,
# 답변 하단에 그 차트와 관련된 다음 질문을 칩으로 보여줘서 매번 직접 타이핑하지
# 않아도 되게 한다. 방금 물어본 것과 겹치는 칩은 아래 렌더링 쪽에서 걸러낸다.
QUICK_REPLIES = {
    "gmv": ["카테고리별로는 어때?", "채널별로는 어때?"],
    "gmv_trend": ["어느 달이 제일 좋았어?", "이번 달 추세는 어때?"],
    "category": ["채널별 매출은 어때?", "세그먼트별로는 어때?"],
    "channel": ["카테고리별 매출은 어때?", "가장 효율 좋은 채널은 어디야?"],
    "segment": ["페르소나별 고객 수는 어때?", "휴면 고객 비중은 얼마나 돼?"],
    "persona": ["이탈 위험 고객엔 어떤 액션이 좋을까?", "휴면 고객은 몇 명이야?"],
    "cohort": ["재구매율은 얼마나 돼?", "전환율을 어떻게 올리면 좋을까?"],
    "funnel": ["장바구니 이탈률은 얼마야?", "전환율을 어떻게 올리면 좋을까?"],
    "rfm": ["세그먼트별 매출은 어때?", "충성 고객은 몇 명이야?"],
    "demographics": ["연령대별로 매출 차이가 있어?", "주 구매 채널은 어디야?"],
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

def _build_system_prompt() -> str:
    """회사명을 하드코딩하지 않고 로그인/데이터셋 선택 때 정해지는
    st.session_state["auth_company_name"]을 그대로 읽어온다. 이렇게 하면 나중에 다른
    회사가 이 앱을 쓰게 되거나(로그인 정보가 바뀌거나), 데이터셋 선택(ATHLEPA/데이콘)이
    바뀌어도 챗봇이 항상 "지금 보고 있는 회사"로 자기소개를 하게 된다. 매번 호출 시점의
    session_state를 읽도록 함수로 만들어뒀다 — 모듈 로드 시점에 한 번만 굳어버리는
    상수로 두면, 로그인 후 회사가 바뀌어도 챗봇은 이전 값을 계속 들고 있게 된다."""
    company = st.session_state.get("auth_company_name", "ATHLEPA")
    return f"""당신은 {company} CRM 대시보드에 내장된 데이터 조회 챗봇입니다.

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
   순위/분포 목록(카테고리별·채널별·세그먼트별·페르소나별·성별·연령대별 매출 등)을
   반환하는 도구를 썼다면, 반환된 항목을 전체 다 읽어주지 마세요 — 그 목록은 이미
   화면 차트에 그대로 나와 있어서, 챗봇이 다시 나열하기만 하면 굳이 물어본 의미가
   없습니다. 대신 1위와 2위의 격차, 눈에 띄게 쏠린 부분, 또는 그로부터 나오는 시사점
   위주로 2~3문장 안에 답하세요(예: "SNS가 1위지만 검색광고와 차이가 크지 않아요"처럼
   숫자 자체보다 숫자 사이의 관계를 짚어주세요). 사용자가 "표로 보여줘", "순서대로 다
   알려줘"처럼 전체 목록을 명시적으로 요청했을 때만 예외적으로 항목을 다 나열하세요.

2. 진단/분석형 (예: "왜 그래?", "무슨 문제야?", "지금 가장 시급한 문제는?")
   → 단순 수치가 아니라 원인과 다음 행동까지 묻는 질문입니다. 반드시 아래 3단계 구조를
   그대로 사용해서 답하세요 (각 단계를 굵게 표시된 소제목으로 구분):
   **결과:** 무엇이 어떻게 됐는지 핵심 수치로 1문장.
   **원인:** 그 결과가 왜 나왔는지, 도구가 반환한 값(예: 페르소나 분포, 채널별 매출 등)에
     근거해서 1~2문장. 도구 결과로 확인되지 않는 원인은 추측이라는 걸 밝히세요.
   **추천 액션:** 다음에 뭘 하면 좋을지 1문장. 이 액션은 검증된 사실이 아니라 제안이라는
     점을 문장 안에서 자연스럽게 드러내세요.
   (이 진단에 실제로 쓰인 지표·기간은 답변 하단에 클릭 가능한 "출처" 태그로 화면에
   자동으로 따로 표시되니, 본문 안에서 "근거:" 같은 문장으로 다시 반복해서 설명하지
   마세요 — 같은 내용이 두 번 보이면 오히려 헷갈립니다.)

3. 전략/의견형 (예: "전환율을 어떻게 올려야 할까?", "뭐가 더 필요할까?")
   → 도구만으로는 답할 수 없는 질문입니다. 답변은 하되, 답변 맨 앞에 반드시
   ":material/lightbulb: **AI 의견** (검증된 사실이 아닌 참고용 제안입니다)"라는 문구를 그대로 넣고
   시작하세요. 가능하면 관련 도구를 먼저 호출해서 실제 지표를 근거로 함께 제시하세요.

4. 무관한 질문 (매장/CRM 데이터와 전혀 관계없는 질문, 예: 날씨, 잡담, 이 챗봇의 정체 등)
   → 도구를 호출하지 말고, "죄송해요, 이 챗봇은 {company} 매장 데이터 관련 질문만 답할
   수 있어요."라고 정중히 안내하세요.

5. 캠페인 제안형 (예: "이탈 위험 고객한테 캠페인 만들어줘", "휴면 고객 리텐션 메시지 짜줘")
   → propose_campaign 도구를 호출하세요. segment는 반드시 실제 페르소나 라벨(신규
   탐색자/충동 구매자/할인 헌터/브랜드 충성 고객/이탈 위험 고객/휴면 고객) 중 하나
   그대로 쓰고, message는 그 세그먼트 특성에 맞는 문구를 직접 작성하세요(문구 자체는
   도구가 대신 써주지 않습니다). 도구가 반환한 대상 인원 수는 실제 데이터 기준이니 그대로
   인용하고 스스로 다른 숫자를 지어내지 마세요. 도구 호출 후 답변 본문은 "아래 제안을
   확인하고 필요하면 문구를 수정한 다음 실행해 주세요." 처럼 짧게만 덧붙이세요 — 화면에
   수정 가능한 카드와 실행 버튼이 자동으로 함께 표시되니 메시지 내용을 본문에 다시
   옮겨 적지 마세요.

규칙:
- 답변은 한국어 존댓말(합니다체)로, 2~4문장 정도로 간결하게 작성하세요.
- 핵심 수치는 **마크다운 볼드체**로 강조하세요.
- 도구 호출 결과에 없는 정보는 추측하지 마세요.
- 숫자를 말할 때는 반드시 비교 기준과 함께 말하세요 (예: "이전 기간 대비", "다른
  세그먼트 평균 대비", "1위 대비"). "전환율이 낮아요"처럼 비교 기준 없이 숫자만
  던지는 문장은 쓰지 마세요. 도구가 반환한 값에 이미 "평균_대비_배율",
  "1위와의_격차_퍼센트", "전월_대비_증감률_퍼센트" 같은 비교 값이 들어있으면 그
  값을 그대로 인용하고, 스스로 다시 나누거나 빼서 계산하지 마세요.
- 원인을 추측할 때 "1) ... 2) ... 3) ..." 처럼 여러 가능성을 나열하며 헤징하지
  마세요. 도구가 반환한 값 중 가장 근거가 뚜렷한 원인 하나를 골라 단정적으로 말하고,
  그 근거가 된 지표를 함께 밝히세요.
- 다음처럼 뻔하고 두루뭉술한 문구는 절대 쓰지 마세요: "다양한 마케팅 전략을
  고려해보세요", "지속적인 모니터링이 필요합니다", "고객과의 소통을 강화하세요",
  "다각도로 분석이 필요합니다", "여러 요인이 복합적으로 작용한 것으로 보입니다".
  이런 말 대신 구체적인 숫자·세그먼트·기간을 넣어서 말하세요.
  나쁜 예: "다양한 전략을 고려해보세요."
  좋은 예: "할인 쿠폰을 받은 고객의 재구매율이 그렇지 않은 고객보다 2.1배 높아요."
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
            system=_build_system_prompt(),
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
            # result도 같이 들고 있는 이유: propose_campaign처럼 도구가 반환한 값(특히
            # 코드로 계산한 대상 인원수) 그대로를 화면 카드에 써야 하는 경우가 있어서다.
            # 그 값을 모델이 답변 텍스트에서 다시 옮겨 적은 걸 파싱하면 모델이 숫자를
            # 살짝 바꿔 쓸 위험이 있으니, 항상 이 원본 result를 그대로 화면에 쓴다.
            tool_calls.append({"name": block.name, "input": block.input, "result": result})
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
    "저번 주보다 매출 늘었어?",
    "구매 퍼널에서 어디가 제일 많이 새?",
    "전환율을 어떻게 올리면 좋을까?",
]

EXECUTION_MODES = ["제안만 (승인 후 실행)", "완전 자동 실행"]

# 플로팅 챗봇(FAB + 패널)을 CSS만으로 화면에 "떠 있게" 만드는 트릭.
# st.container(key="...")가 만드는 div에 실제 클래스 이름(st-key-<key>)이 그대로 붙는다는
# 걸 이용해서, 그 div에 position:fixed를 강제로 씌운다. Streamlit 버전이 바뀌면 이 클래스
# 구조가 달라져서 깨질 수 있는데, 그럴 땐 브라우저 개발자 도구로 실제 클래스명을 다시
# 확인해서 여기 선택자만 맞춰주면 된다.
AI_CHAT_CSS = """
<style>
/* 텍스트 라벨("AI 어시스턴트") 대신 아이콘 하나만 있는 동그란 FAB. 안 읽은 메시지가
   있으면 숫자만 같이 보여준다(예: "💬 2"). p 태그 margin을 0으로 맞추는 것도
   피드백 버튼과 같은 이유(아이콘이 원 중앙에서 살짝 밀려 보이는 문제 방지). */
div[class*="st-key-ai-fab"] {
    position: fixed; right: 28px; bottom: 28px; z-index: 999999; width: auto;
}
div[class*="st-key-ai-fab"] button {
    border-radius: 50%; background: var(--athlepa-primary); color: #fff; border: none;
    box-shadow: 0 6px 18px rgba(124,58,237,0.35); font-weight: 600;
    width: 56px !important; height: 56px !important; padding: 0 !important; font-size: 15px !important;
    display: flex !important; align-items: center !important; justify-content: center !important;
}
div[class*="st-key-ai-fab"] button p {
    margin: 0 !important; display: flex !important; align-items: center !important; gap: 3px;
}
div[class*="st-key-ai-fab"] button:hover { background: var(--athlepa-primary-hover); color: #fff; }

/* 패널이 열려 있을 때, 화면 전체를 덮는 투명 버튼(빈 공간 클릭 시 닫기용).
   패널(z-index 더 높음)이 그 위를 덮고 있어서, 패널 '바깥'을 눌렀을 때만 이 버튼이 눌린다. */
div[class*="st-key-ai-overlay"] { position: fixed; inset: 0; z-index: 999997; }
div[class*="st-key-ai-overlay"] button {
    width: 100vw; height: 100vh; background: transparent; border: none; cursor: default;
}

/* 380px 폭에 기본 글씨 크기 그대로 두니 글씨가 패널에 비해 커 보인다는 피드백이
   있어서, 패널을 조금 넓히고(380→440px, 세로도 72→80vh) 안쪽 글씨는 살짝 줄였다. */
div[class*="st-key-ai-panel"] {
    position: fixed; right: 28px; bottom: 96px; width: 440px; max-height: 80vh;
    background: #fff; border: 1px solid var(--athlepa-border); border-radius: 14px;
    box-shadow: 0 16px 40px rgba(17,16,24,0.18); z-index: 999998;
    overflow-y: auto; padding: 8px 14px 12px;
}
div[class*="st-key-ai-panel"] [data-testid="stChatMessage"] p,
div[class*="st-key-ai-panel"] [data-testid="stChatMessage"] li,
div[class*="st-key-ai-panel"] [data-testid="stMarkdownContainer"] p {
    font-size: 13.5px !important; line-height: 1.55 !important;
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

/* "이어서 물어보기" 퀵리플라이 칩. 출처 태그(흰 배경, 정보 확인용)와 헷갈리지 않도록
   보라색 계열로 채워서 "누르면 다음 액션(질문)이 일어난다"는 걸 시각적으로 구분했다. */
div[class*="st-key-ai-quick-"] { margin-top: 4px; }
div[class*="st-key-ai-quick-"] p {
    font-size: 10.5px !important; color: var(--athlepa-muted-text) !important; margin: 0 0 4px !important;
}
div[class*="st-key-ai-quick-"] button {
    font-size: 11.5px !important; padding: 3px 12px !important; height: auto !important;
    min-height: 0 !important; border-radius: 999px !important; border: 1px solid transparent !important;
    background: var(--athlepa-secondary) !important; color: var(--athlepa-primary-hover) !important;
    margin: 0 4px 4px 0 !important; font-weight: 500 !important;
}
/* 위의 일반 "p" 규칙(캡션용)이 버튼 라벨을 감싼 <p>까지 덮어써서 버튼 글씨가 회색으로
   보이던 문제 — 선택자에 button을 한 겹 더 넣어 더 구체적으로 만들어서(우선순위로 이김)
   버튼 안 글씨는 버튼 자체의 color(보라색)를 그대로 물려받게 강제한다. */
div[class*="st-key-ai-quick-"] button p {
    color: inherit !important; font-size: 11.5px !important; margin: 0 !important;
}
div[class*="st-key-ai-quick-"] button:hover {
    background: var(--athlepa-primary) !important; color: #fff !important;
}

/* 👍/👎 버튼: 이모지가 배경(원형) 정중앙에 오도록 폭/높이를 같게 고정하고 flex로
   가운데 정렬한다. Streamlit이 버튼 라벨을 <p> 태그로 감싸면서 기본 margin이 붙어
   이모지가 배경 밖으로 살짝 밀려 보이던 문제라, p 태그의 margin/line-height도 같이
   0으로 맞춰준다. type="primary"(눌린 상태)일 때 배경색이 사각형으로 보이던 것도
   border-radius를 100%로 강제해서 항상 동그랗게 나오게 했다. */
div[class*="st-key-ai-fb-"] button {
    padding: 0 !important; font-size: 13px !important; width: 26px !important; height: 26px !important;
    min-height: 0 !important; min-width: 0 !important; border: none !important; opacity: 0.55;
    border-radius: 50% !important; display: flex !important; align-items: center !important;
    justify-content: center !important; line-height: 1 !important;
}
div[class*="st-key-ai-fb-"] button p {
    margin: 0 !important; line-height: 1 !important; display: flex !important; align-items: center !important;
}
div[class*="st-key-ai-fb-"] button:hover { opacity: 1; }

/* 차트/카드 전체를 클릭하면 AI 챗봇이 분석해주는 기능용 스타일. dashboard/charts.py
   쪽에서 st.container(key="click_xxx")로 차트를 감싸고, 그 안에 st.button(key="ask_xxx")
   (버튼 key는 "ask_"로 시작해야 함)로 ask_chatbot()을 부르면, 이 스타일이 버튼을
   투명하게 만들어서 카드 전체를 덮어씌운다 — 버튼 자체를 누르는 게 아니라 카드
   아무 곳이나 눌러도 되는 것처럼 보이게 하는 트릭이다.

   Streamlit은 위젯마다 자동으로 "st-key-<위젯키>"라는 래퍼 div를 만드는데, 이 래퍼가
   자체적으로 position:relative를 갖고 있어서, 버튼만 position:absolute로 늘려도
   "카드 전체" 기준이 아니라 "이 래퍼 자신" 기준으로 늘어나 버린다(래퍼 자신은 버튼
   크기만큼만 좁게 차지하므로 결국 아주 작은 영역만 늘어나는 것처럼 보임). 그래서
   래퍼(div[class*="st-key-ask_"])까지 같이 카드 전체 크기로 늘려줘야 실제로 카드
   전체가 클릭 가능해진다. */
div[class*="st-key-click_"] {
    position: relative; cursor: pointer;
    transition: border-color 0.15s ease, transform 0.15s ease, box-shadow 0.15s ease;
}
div[class*="st-key-click_"]:hover {
    border-color: var(--athlepa-primary);
    transform: translateY(-2px);
    box-shadow: 0 8px 20px rgba(124,58,237,0.12);
}
div[class*="st-key-click_"] div[class*="st-key-ask_"] {
    position: absolute !important; inset: 0 !important; width: 100% !important; height: 100% !important;
    margin: 0 !important; padding: 0 !important;
}
div[class*="st-key-click_"] div[class*="st-key-ask_"] div.stButton {
    position: absolute !important; inset: 0 !important; width: 100% !important; height: 100% !important;
    margin: 0 !important; padding: 0 !important;
}
div[class*="st-key-click_"] div[class*="st-key-ask_"] button {
    position: absolute !important; inset: 0 !important; width: 100% !important; height: 100% !important;
    opacity: 0 !important; z-index: 5 !important; border: none !important; cursor: pointer !important;
    padding: 0 !important; margin: 0 !important; min-width: 0 !important; max-width: none !important;
}

div[class*="st-key-ai_chat_form"] button {
    white-space: nowrap; padding-left: 10px !important; padding-right: 10px !important;
}

/* 캠페인 제안 카드. 답변 본문과 구분되게 살짝 카드처럼 보이도록 테두리/배경을 준다. */
div[class*="st-key-ai-campaign-"] {
    border: 1px solid var(--athlepa-border); border-radius: 10px;
    padding: 8px 12px 4px; margin: 6px 0 8px; background: var(--athlepa-secondary);
}
div[class*="st-key-ai-campaign-"] textarea { font-size: 12.5px !important; }
div[class*="st-key-ai-campaign-"] [data-testid="stCaptionContainer"] p {
    font-weight: 600 !important; color: var(--athlepa-primary-hover) !important;
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


def ask_chatbot(question: str):
    """대시보드 등 다른 페이지의 'AI 분석하기' 버튼에서 호출한다. 플로팅 챗봇 패널을
    열고 이 질문을 예약해두면, render_floating_chat()이 다음 렌더링에서 실제 답변까지
    받아온다 (직접 여기서 API를 호출하지 않는 이유: 플로팅 패널은 Home.py의 최상위
    스크립트에서만 렌더링되므로, 다른 페이지 스크립트 안에서 곧바로 메시지를 그릴 수 없다)."""
    st.session_state["pending_question"] = question
    st.session_state["chat_panel_open"] = True
    st.rerun()


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
    campaign_proposal = None
    for call in tool_calls:
        desc = _describe_tool_call(call["name"], call["input"])
        thinking.append(desc)
        chart_key = TOOL_LABELS.get(call["name"], {}).get("chart_key")
        dedupe_key = (call["name"], chart_key)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            sources.append({"label": desc, "chart_key": chart_key})
        if call["name"] == "propose_campaign" and call.get("result"):
            campaign_proposal = dict(call["result"])  # 마지막 제안만 카드로 남긴다

    # "완전 자동 실행" 모드면 제안이 나오자마자 바로 기록까지 남긴다. "제안만" 모드면
    # 카드만 보여주고, 실제 기록은 사용자가 화면에서 실행 버튼을 눌러야 남는다.
    if campaign_proposal and st.session_state.get("ai_execution_mode") == EXECUTION_MODES[1]:
        try:
            _execute_campaign(campaign_proposal, campaign_proposal["메시지"], campaign_proposal["채널"])
        except Exception as e:
            campaign_proposal["execute_error"] = str(e)

    st.session_state.chat_messages.append({"role": "assistant", "content": answer})
    st.session_state.chat_meta.append(
        {"sources": sources, "thinking": thinking, "feedback": None, "campaign_proposal": campaign_proposal}
    )


def _execute_campaign(proposal: dict, message: str, channel: str):
    """캠페인 제안을 '실행'한다 — 여기서 실행은 실제 발송(카카오톡/SMS/이메일 등)이
    아니라, automation 탭·성과 대시보드가 보는 campaign_history 테이블(Supabase)에
    기록을 남기는 것까지만 의미한다(팀 합의 사항). automation/email_sender.py의
    save_history()를 그대로 재사용해서, 자동화 탭 발송 이력 화면에도 이 캠페인이 똑같이
    "AI 챗봇 제안" 상태로 나타나게 한다 — 실제 발송 여부는 그 화면에서 담당자가 최종
    결정한다. 이 파일에서 automation/ 폴더 파일을 직접 수정하지는 않는다(기존 함수를
    가져다 쓰기만 함)."""
    from automation.email_sender import save_history

    campaign_id = save_history(
        segment=proposal["세그먼트"],
        copy=message,
        count=proposal.get("대상_인원") or 0,
        status="AI 챗봇 제안",
        approval_mode=st.session_state.get("ai_execution_mode", EXECUTION_MODES[0]),
    )
    proposal["executed"] = True
    # save_history()가 campaign_id를 만들어서 돌려주는 최신 버전인지 아닌지에 따라
    # None이 올 수도 있다(automation/email_sender.py가 아직 구버전이면 반환값이 없음) —
    # 그래도 기록 자체는 남았으니 화면에 "None"이라고 이상하게 보이지 않게 처리.
    proposal["campaign_id"] = campaign_id or "(자동 생성 ID 없음 — CSV에는 기록됨)"
    proposal["채널"] = channel
    proposal["메시지"] = message


def _jump_to_chart(chart_key: str, label: str = ""):
    """출처 태그를 클릭하면 대시보드 페이지로 이동하면서, 어떤 차트를 보고 있었는지
    session_state에 남겨둔다. dashboard/charts.py 쪽에서 이 값(dashboard_highlight_chart)을
    읽어서 해당 차트를 강조 표시해주는 코드는 아직 없다 — 그건 그 파일 담당자와 별도로
    맞춰야 한다. 지금은 페이지 이동까지만 이 파일 안에서 완결된다.

    st.toast는 딱 한 번의 rerun(=페이지 이동 1번)까지는 화면에 남아있는 Streamlit
    기능이라, 페이지가 바뀐 직후에도 "어디로, 왜 이동했는지"가 잠깐 보인다. 이게 없으면
    클릭했을 때 로딩만 살짝 뜨고 아무 반응이 없는 것처럼 느껴진다는 피드백이 있었다."""
    st.toast(f"'{label}' 관련 차트로 이동했어요 (차트 강조 표시는 아직 준비 중이에요)", icon="📊")
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


def _render_campaign_proposal(idx: int, proposal: dict):
    """캠페인 제안 카드 — 세그먼트/대상 인원은 읽기 전용(코드가 계산한 값), 채널·문구는
    실행 전에 수정할 수 있다. '실행'을 누르기 전까지는 아무 것도 기록되지 않는다."""
    with st.container(key=f"ai-campaign-{idx}"):
        audience = proposal.get("대상_인원")
        st.caption(f"📣 캠페인 제안 · {proposal['세그먼트']}" + (f" · 대상 약 {audience:,}명" if audience is not None else ""))

        if proposal.get("executed"):
            st.success(f"✅ 실행 완료 — 발송 이력에 기록했어요 (캠페인 ID: {proposal.get('campaign_id', '-')})")
            return

        if proposal.get("execute_error"):
            st.error(f"실행 중 문제가 생겼어요: {proposal['execute_error']}")

        channel_options = CAMPAIGN_CHANNELS
        default_channel = proposal.get("채널") if proposal.get("채널") in channel_options else channel_options[0]
        channel = st.selectbox("채널", channel_options, key=f"campaign_channel_{idx}", index=channel_options.index(default_channel))
        message = st.text_area("메시지 (수정 가능)", value=proposal.get("메시지", ""), key=f"campaign_msg_{idx}", height=100)

        if st.button("🚀 실행 (발송 이력에 기록)", key=f"campaign_exec_{idx}"):
            try:
                _execute_campaign(proposal, message, channel)
            except Exception as e:
                proposal["execute_error"] = str(e)
            st.rerun()


def _render_message(idx: int, msg: dict, meta: dict, is_last: bool = False):
    with st.chat_message(msg["role"]):
        if msg["role"] != "assistant":
            st.markdown(msg["content"])
            return

        # "생각 과정"과 "출처 태그"가 똑같은 라벨(도구명+기간)을 두 번 보여주는 중복이
        # 있었다 — 도구를 1번만 호출한(가장 흔한) 경우엔 출처 태그 하나로 이미 충분하므로
        # 생각 과정 expander를 아예 숨기고, 도구를 여러 번 호출했을 때만(비교 질문처럼
        # 출처 태그만으로는 안 보이는 호출 순서가 있을 때) 펼쳐서 볼 수 있게 남겨둔다.
        thinking = meta.get("thinking") or []
        if len(thinking) > 1:
            with st.expander("🔍 생각 과정 보기", expanded=False):
                for step in thinking:
                    st.caption(f"· {step}")

        render_chat_text(msg["content"])

        proposal = meta.get("campaign_proposal")
        if proposal:
            _render_campaign_proposal(idx, proposal)

        sources = meta.get("sources") or []
        if sources:
            with st.container(key=f"ai-src-{idx}"):
                for s_i, source in enumerate(sources):
                    if source["chart_key"]:
                        if st.button(f"📊 출처: {source['label']}", key=f"src_{idx}_{s_i}"):
                            _jump_to_chart(source["chart_key"], source["label"])
                    else:
                        st.button(f"출처: {source['label']}", key=f"src_{idx}_{s_i}", disabled=True)

        # 가장 최근 답변에서만 후속 질문 칩을 보여준다 — 지난 대화 전체에 계속 붙어있으면
        # 스크롤할수록 칩이 계속 쌓여서 지저분해진다. 방금 쓴 차트(chart_key) 기준으로
        # 관련 질문을 뽑고, 방금 사용자가 이미 물어본 것과 겹치는 칩은 빼준다.
        if is_last and sources:
            asked = {m["content"] for m in st.session_state.chat_messages if m["role"] == "user"}
            seen_keys, quick = set(), []
            for source in sources:
                for q in QUICK_REPLIES.get(source["chart_key"], []):
                    if q not in seen_keys and q not in asked:
                        seen_keys.add(q)
                        quick.append(q)
            quick = quick[:3]
            if quick:
                with st.container(key=f"ai-quick-{idx}"):
                    st.caption("이어서 물어보기")
                    for q_i, q in enumerate(quick):
                        if st.button(q, key=f"quick_{idx}_{q_i}"):
                            st.session_state["pending_question"] = q
                            st.rerun()

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
                    "'제안만'을 고르면 캠페인 제안 카드에서 문구를 확인·수정한 뒤 실행 "
                    "버튼을 눌러야 발송 이력에 기록되고, '완전 자동 실행'을 고르면 제안이 "
                    "나오자마자 자동으로 기록돼요. 여기서 '실행'은 실제 발송이 아니라 "
                    "발송 이력에 기록을 남기는 것까지예요 — 실제 발송 여부는 자동화 탭에서 "
                    "따로 결정해요."
                )

            if not st.session_state.chat_messages:
                company = st.session_state.get("auth_company_name", "ATHLEPA")
                st.markdown(
                    f"안녕하세요! {company} CRM AI 어시스턴트입니다.  \n"
                    "고객 데이터 분석, 세그먼트 조회 등 원하는 걸 물어보세요."
                )
                cols = st.columns(2)
                for i, q in enumerate(EXAMPLE_QUESTIONS):
                    with cols[i % 2]:
                        if st.button(q, key=f"example_q_{i}", use_container_width=True):
                            pending_question = q

            last_idx = len(st.session_state.chat_messages) - 1
            for idx, (msg, meta) in enumerate(zip(st.session_state.chat_messages, st.session_state.chat_meta)):
                _render_message(idx, msg, meta, is_last=(idx == last_idx))

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
        if is_open:
            fab_label = "✕"
        elif unseen > 0:
            fab_label = f"💬 {unseen}"
        else:
            fab_label = "💬"
        if st.button(fab_label, key="ai_fab_btn", help="AI 어시스턴트"):
            st.session_state["chat_panel_open"] = not is_open
            st.rerun()
