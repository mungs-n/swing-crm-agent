"""
담당자: 탭1 AI 인사이트 담당
작업 내용: Claude API 연동, AI 인사이트 패널, RFM/코호트 상세 분석 통합
"""

import re
import html
import streamlit as st
import plotly.graph_objects as go
import anthropic
import os
import pandas as pd
from datetime import datetime


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


def compute_recency_days(users: pd.DataFrame, orders: pd.DataFrame, reference_date=None) -> pd.Series:
    """전체 유저(users.csv 기준) 대상으로 '기준일로부터 마지막 구매까지 경과일'을 계산한다.

    reference_date를 지정하면 그 날짜를 기준으로 계산한다 (기본은 orders 내
    가장 최근 주문일). AI 인사이트에서는 선택한 분석 기간의 종료일을
    reference_date로 넘겨서, '그 시점 기준으로' 휴면/이탈 위험 여부를 판단한다.

    한 번도 구매하지 않은 유저는 orders 테이블에 나타나지 않으므로 빠뜨리기
    쉽다. 이 함수는 구매 이력이 있으면 마지막 구매일을, 없으면 가입일을
    기준으로 삼아 users.csv의 모든 유저에 대해 값을 계산한다.
    """
    users = users.copy()
    orders = orders.copy()
    users["signup_date"] = pd.to_datetime(users["signup_date"])
    orders["order_date"] = pd.to_datetime(orders["order_date"])

    if reference_date is not None:
        latest_date = pd.Timestamp(reference_date)
    elif len(orders):
        latest_date = orders["order_date"].max()
    else:
        latest_date = users["signup_date"].max()

    if len(orders):
        last_order_date = orders.groupby("user_id")["order_date"].max()
    else:
        last_order_date = pd.Series(dtype="datetime64[ns]")
    signup_date = users.set_index("user_id")["signup_date"]

    reference = last_order_date.reindex(signup_date.index)
    reference = reference.fillna(signup_date)

    return (latest_date - reference).dt.days


def compute_repeat_purchase_rate(orders: pd.DataFrame) -> float:
    """2회 이상 구매한 고객 비율(%). (구매 이력이 있는 고객 중 기준)"""
    if orders.empty:
        return 0.0
    order_counts = orders.groupby("user_id").size()
    if order_counts.shape[0] == 0:
        return 0.0
    return (order_counts >= 2).sum() / order_counts.shape[0] * 100


def compute_rfm(orders: pd.DataFrame) -> pd.DataFrame:
    """유저별 RFM(Recency, Frequency, Monetary) 계산"""
    if orders.empty:
        return pd.DataFrame(columns=["user_id", "frequency", "monetary", "last_order", "recency"])
    latest_date = orders["order_date"].max()

    rfm = orders.groupby("user_id").agg(
        frequency=("order_id", "count"),
        monetary=("total_amount", "sum"),
        last_order=("order_date", "max"),
    )
    rfm["recency"] = (latest_date - rfm["last_order"]).dt.days
    return rfm.reset_index()


def compute_cohort_retention(users: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """가입월 기준 코호트 리텐션(%) 피벗 테이블 계산

    코호트 = 회원가입 월. 각 코호트 전체 인원 대비, 가입 후 N개월차에
    '구매'가 있었던 회원 비율(%)을 계산한다.
    orders 데이터의 관측 시작월 이후에 가입한 코호트만 포함한다.
    """
    if orders.empty:
        return pd.DataFrame()

    users = users.copy()
    orders = orders.copy()

    users["signup_date"] = pd.to_datetime(users["signup_date"])
    users["cohort_month"] = users["signup_date"].dt.to_period("M")

    obs_start_month = orders["order_date"].min().to_period("M")
    users = users[users["cohort_month"] >= obs_start_month]

    merged = orders.merge(users[["user_id", "cohort_month"]], on="user_id", how="inner")
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

    - period_orders / period_events: 선택한 기간(start_date~end_date) 안의 데이터만
    - orders_until_end: 휴면 판정처럼 기간 시작 이전 구매 이력도 봐야 하는 계산을 위해,
      기간 '종료일'까지의 전체 주문 이력 (시작일 이전 것도 포함)

    주의: users["signup_date"]/orders["order_date"]/events["timestamp"]가 이미
    datetime으로 변환돼 있다고 가정한다 (호출 전에 변환해서 넘길 것).
    """
    period_orders = orders[(orders["order_date"].dt.date >= start_date) & (orders["order_date"].dt.date <= end_date)]
    period_events = events[(events["timestamp"].dt.date >= start_date) & (events["timestamp"].dt.date <= end_date)]
    orders_until_end = orders[orders["order_date"].dt.date <= end_date]
    return period_orders, period_events, orders_until_end


def get_dashboard_summary(start_date, end_date) -> str:
    """선택한 분석 기간(start_date~end_date) 데이터만 반영한 요약 (Claude에게 넘길 컨텍스트)"""
    users, orders, events = load_data()

    period_orders, period_events, orders_until_end = get_period_slices(users, orders, events, start_date, end_date)

    # load_data()는 users["signup_date"]를 datetime으로 변환해두지 않으므로 여기서 변환
    signup_date = pd.to_datetime(users["signup_date"])
    # 주의: 예전 버전은 signup_date <= end_date만 봤는데, 프리셋(최근 7일/4주/3개월 등)은
    # 전부 end_date가 데이터의 최신일(max_date)로 동일해서 total_users가 어떤 기간을
    # 선택해도 항상 같은 값(예: 전체 1000명)으로 고정되는 문제가 있었다. 이 값은 AI
    # 리포트에 '전체 가입 고객 수'로 그대로 노출되므로, 실제로 선택한 기간(start_date~
    # end_date) 동안 가입한 고객 수로 범위를 좁혀서 기간을 바꾸면 값도 같이 바뀌게 한다.
    #
    # recommend_segment()의 total_users와는 일부러 다르게 계산한다 — 그쪽은 화면에
    # 노출되는 값이 아니라 dormant_count/at_risk_count 비율의 분모로 쓰이는데, 그
    # 두 카운트가 가입 시점과 무관하게 '전체 고객' 기준으로 세어지는 값이라 분모도
    # 같은 전체 고객 기준이어야 비율이 의미를 가진다. 여기(화면에 보여줄 값)만
    # 기간 내 신규가입자 수로 좁힌다.
    total_users = users[
        (signup_date.dt.date >= start_date) & (signup_date.dt.date <= end_date)
    ]["user_id"].nunique()
    active_users_period = period_events["user_id"].nunique()

    gmv = period_orders["total_amount"].sum()
    order_count = len(period_orders)
    aov = gmv / order_count if order_count > 0 else 0

    purchase_users = period_events[period_events["event_type"] == "purchase"]["user_id"].nunique()
    all_visitor_users = period_events["user_id"].nunique()
    conversion_rate = (purchase_users / all_visitor_users * 100) if all_visitor_users > 0 else 0

    cart_users = period_events[period_events["event_type"] == "add_to_cart"]["user_id"].nunique()
    cart_abandon_rate = (1 - purchase_users / cart_users) * 100 if cart_users > 0 else 0

    recency_days = compute_recency_days(users, orders_until_end, reference_date=end_date)
    dormant_users = (recency_days >= 90).sum()

    repeat_purchase_rate = compute_repeat_purchase_rate(period_orders)

    user_revenue = period_orders.groupby("user_id")["total_amount"].sum().sort_values(ascending=False)
    if len(user_revenue) > 0 and user_revenue.sum() > 0:
        top10_n = max(1, int(len(user_revenue) * 0.1))
        top10_revenue_share = user_revenue.iloc[:top10_n].sum() / user_revenue.sum() * 100
    else:
        top10_revenue_share = 0

    retention = compute_cohort_retention(users, period_orders)
    avg_month1_retention = retention[1].mean() if (not retention.empty and 1 in retention.columns) else 0

    return f"""
    분석 대상 기간: {start_date} ~ {end_date}
    - 해당 기간 내 가입 고객 수: {total_users}명
    - 해당 기간 활성 고객 수: {active_users_period}명
    - GMV(총 거래액): ₩{gmv:,.0f}
    - AOV(평균 주문 금액): ₩{aov:,.0f}
    - 구매 전환율: {conversion_rate:.1f}%
    - 장바구니 이탈률: {cart_abandon_rate:.1f}%
    - 휴면 고객({end_date} 기준 90일 이상 미구매, 무구매 고객 포함): {dormant_users}명
    - 재구매율(해당 기간 내 2회 이상 구매 고객 비율): {repeat_purchase_rate:.1f}%
    - 상위 10% 고객의 매출 기여도: {top10_revenue_share:.1f}%
    - 신규 고객의 1개월차 평균 재방문율: {avg_month1_retention:.1f}%
    """


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

    period_orders, period_events, orders_until_end = get_period_slices(users, orders, events, start_date, end_date)

    # get_dashboard_summary()의 total_users와는 다르게 '기간 내 신규가입자'가 아니라
    # '누적 전체 가입자'를 쓴다. dormant_count/at_risk_count는 가입 시점과 무관하게
    # 전체 고객 기준으로 세는 값이라, 분모도 같은 전체 고객 기준이어야 비율이 의미를
    # 가진다 — 기간 내 신규가입자 수로 나누면 분모가 지나치게 작아져서, 이 함수가
    # 원래 고치려던 문제("어떤 기간을 골라도 항상 휴면 고객만 1등으로 잡히는 문제")가
    # 다른 경로로 재발한다. (일부러 다르게 둔 것이며 실수가 아님)
    total_users = users[users["signup_date"].dt.date <= end_date]["user_id"].nunique()

    # 선택 기간 종료일 기준 경과일 (휴면/이탈위험은 스냅샷 성격이라 기간 시작일보다는 종료일 기준이 자연스러움)
    recency_days = compute_recency_days(users, orders_until_end, reference_date=end_date)
    dormant_count = (recency_days >= 90).sum()
    at_risk_count = ((recency_days >= 45) & (recency_days < 90)).sum()

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
    candidates = [
        ("휴면 고객", (dormant_count / total_users) / 0.15 if total_users else 0),
        ("이탈 위험 고객", (at_risk_count / total_users) / 0.10 if total_users else 0),
        ("신규 탐색자", max(0.0, (0.3 - new_user_purchase_rate) / 0.3) if len(new_users) > 0 else 0.0),
        ("이탈 위험 고객", (cart_abandon_rate / 100) / 0.45),
        ("할인 구매자", (coupon_rate / 100) / 0.50),
        ("브랜드 충성 고객", (repeat_purchase_rate / 100) / 0.70),
        ("충동 구매자", 0.5),  # 다른 이슈가 뚜렷하지 않을 때를 위한 기본값
    ]

    best_segment, _ = max(candidates, key=lambda pair: pair[1])
    return best_segment


# ---------------------------------------------------------
# Claude API 호출
# ---------------------------------------------------------

def run_ai_analysis(summary):
    """Claude API 호출해서 인사이트 생성 (컨설팅 리포트 형식, 스트리밍)"""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2400,
        system="""당신은 이커머스 CRM 데이터 분석 전문가입니다.
대시보드 실무자가 의사결정에 바로 활용할 수 있는 전문적인 분석 리포트를
한국어 존댓말(합니다체)로 작성하세요.

다음 4개 섹션 구조로 작성하세요 (총 7~10문장 분량). 아래 소제목 문구를
정확히 그대로 사용하세요 (화면에서 이 문구를 기준으로 탭을 구성합니다):

### 🎯 핵심 진단
가장 시급한 문제 1가지와 그 이유를 1~2문장으로 명확히 제시

### 🔍 원인 분석
관련 지표들을 근거로 왜 이런 현상이 발생했는지 2~3문장으로 설명
(이탈률, 재구매율, 코호트 재방문율 등 지표 간의 연결고리를 짚되,
서로 다른 고객군 기준의 지표를 인과관계처럼 단정하지 말고
"~로 추정됩니다", "~일 가능성이 있습니다"처럼 상관관계 수준으로 서술할 것)

### 📊 정량적 임팩트
방치 시 예상되는 손실, 혹은 개선 시 기대 효과를 구체적 수치로 제시 (2문장)

### ✅ 실행 권장사항
가장 우선순위 높은 액션 1~2가지만 간결하게 제시. 이 리포트를 읽고 바로
"캠페인 자동화" 버튼을 눌러 해당 세그먼트에 이메일을 발송하는 흐름으로
이어지므로, 여러 단계로 나뉜 세부 일정보다는 "이 세그먼트에 어떤
메시지/혜택을 보낼지" 수준의 간단하고 실행 가능한 문장으로 작성

화면에는 보이지 않지만 시스템이 자동으로 읽어서 캠페인 자동화 버튼에 쓰는
5번째 섹션도 반드시 마지막에 추가하세요:

### 🎯 타겟 세그먼트
바로 위 "실행 권장사항"에서 캠페인을 보내자고 제안한 고객군과 반드시 일치하는
세그먼트 키 하나만, 다른 설명 없이 정확히 그대로 적으세요.
사용 가능한 키(이 중 하나만): new_explorer(신규 탐색자) / impulsive_buyer(충동 구매자)
/ discount_hunter(할인 구매자) / brand_loyalist(브랜드 충성 고객) / churn_risk(이탈 위험 고객)
/ dormant(휴면 고객)

규칙:
- 반드시 위 5개 섹션을 ### 소제목(이모지 포함, 문구 그대로)으로 구분해서 작성
- "실행 권장사항" 섹션에서 언급한 캠페인 대상 고객군과 "🎯 타겟 세그먼트" 섹션의
  키는 반드시 같은 세그먼트를 가리켜야 합니다. 리포트에 쓴 내용과 실제로 실행되는
  캠페인 대상이 어긋나면 안 됩니다
- 문장 끝은 반드시 존댓말로 마무리 ("~합니다", "~권장드립니다", "~필요합니다" 등).
  "~하라", "~할 것", "~이다" 같은 반말/명령형 어미는 절대 사용하지 말 것
- 핵심 수치나 세그먼트명, 그리고 문장에서 가장 중요한 키워드는 **마크다운 볼드체**
  (**이렇게**)로 강조할 것. [대괄호]는 사용하지 말 것
- 볼드체 강조는 네 섹션(핵심 진단/원인 분석/정량적 임팩트/실행 권장사항) 전부에
  예외 없이 최소 1곳 이상 있어야 함. 특히 핵심 진단과 원인 분석 섹션도
  절대 빠뜨리지 말고 핵심 수치나 세그먼트명을 볼드체로 표시할 것
- 주어진 데이터에 근거해서만 서술하고, 없는 정보는 추측하지 말 것
- 서로 다른 고객 세그먼트(예: 전체 고객 vs 신규 고객)의 지표를 섞어서
  단정적 인과관계로 서술하지 말 것 — 상관관계 수준의 신중한 표현을 사용할 것
- 어떤 수치를 "높다/낮다/충분하다/부족하다"고 평가할 때는, 비교할 명확한
  기준을 함께 제시할 수 있는 경우에만 그렇게 서술할 것
- 주어진 지표를 전부 다 언급할 필요는 없음. 실제로 논리적 근거가 뚜렷한
  지표 3~4개만 선택해서 사용할 것
- 본문 안에는 이모지·표를 넣지 말 것 (소제목에만 사용). 실무 보고서 톤 유지""",
        messages=[
            {"role": "user", "content": f"다음 데이터를 분석해주세요:\n{summary}"}
        ]
    ) as stream:
        for text in stream.text_stream:
            yield text

        final_message = stream.get_final_message()
        if final_message.stop_reason == "max_tokens":
            yield "\n\n> ⚠️ 응답 길이 제한으로 내용이 일부 잘렸습니다. '다시 분석하기'를 눌러주세요."


# ---------------------------------------------------------
# AI 응답을 4개 섹션으로 파싱 (탭 UI용)
# ---------------------------------------------------------

TAB_LABELS = ["🎯 핵심 진단", "🔍 원인 분석", "📊 정량적 임팩트", "✅ 실행 권장사항"]
TAB_KEYWORDS = ["핵심 진단", "원인 분석", "정량적 임팩트", "실행 권장사항"]


def parse_ai_sections(text: str) -> dict:
    """### 소제목 기준으로 텍스트를 섹션별로 분리. 스트리밍 도중(마지막 섹션이
    아직 끝나지 않은 상태)에 호출해도 그때까지 완성된 내용을 그대로 반환한다."""
    pattern = r"###\s*(.+?)\n(.*?)(?=\n###|\Z)"
    matches = re.findall(pattern, text, flags=re.S)
    return {title.strip(): content.strip() for title, content in matches}


def find_section(sections: dict, keyword: str):
    for title, content in sections.items():
        if keyword in title:
            return content
    return None


# components/campaign_builder.py의 PERSONAS 딕셔너리 키와 정확히 동일해야 하는
# 매핑. AI가 리포트에 적은 세그먼트 키를, 캠페인 자동화 페이지의 selectbox가
# 기본값으로 알아볼 수 있는 한글 라벨로 되돌리는 데 사용한다. (charts.py의
# PERSONA_KR과는 "할인 구매자"/"할인 헌터"처럼 라벨이 미묘하게 달라서 그대로
# 재사용하면 안 된다 — 실제 사용처인 campaign_builder.py 기준으로 맞춘다.)
PERSONA_LABEL_KR = {
    "new_explorer": "신규 탐색자",
    "impulsive_buyer": "충동 구매자",
    "discount_hunter": "할인 구매자",
    "brand_loyalist": "브랜드 충성 고객",
    "churn_risk": "이탈 위험 고객",
    "dormant": "휴면 고객",
}


def extract_target_segment(sections: dict) -> str | None:
    """AI 응답의 '🎯 타겟 세그먼트' 섹션에서 세그먼트 키를 읽어 한글 라벨로 반환한다.

    이 값은 '실행 권장사항' 섹션과 같은 응답 안에서 함께 생성되므로, 화면에
    보이는 리포트 내용과 '캠페인 자동화 시작하기' 버튼이 실제로 타겟하는
    세그먼트가 항상 일치한다. 섹션이 없거나 알 수 없는 값이면 None을 반환하고,
    호출 쪽에서 recommend_segment()로 대체한다."""
    content = find_section(sections, "타겟 세그먼트")
    if not content:
        return None
    match = re.search(r"|".join(PERSONA_LABEL_KR.keys()), content)
    return PERSONA_LABEL_KR[match.group(0)] if match else None


# ---------------------------------------------------------
# 상세 분석: RFM 산포도 + 코호트 리텐션 히트맵
# ---------------------------------------------------------

def render_rfm_scatter(rfm: pd.DataFrame):
    if rfm.empty:
        st.info("표시할 데이터가 없습니다.")
        return
    fig = go.Figure(
        data=go.Scatter(
            x=rfm["frequency"],
            y=rfm["monetary"],
            mode="markers",
            marker=dict(
                size=rfm["monetary"],
                sizemode="area",
                sizeref=2.0 * rfm["monetary"].max() / (40.0 ** 2),
                sizemin=4,
                color=rfm["recency"],
                colorscale="RdYlBu",
                colorbar=dict(title="최근성(일)"),
                line=dict(width=0.5, color="rgba(0,0,0,0.2)"),
                opacity=0.85,
            ),
            hovertemplate=(
                "구매 빈도: %{x}<br>"
                "누적 구매액: ₩%{y:,.0f}<br>"
                "최근성: %{marker.color}일<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        xaxis_title="구매 빈도",
        yaxis_title="누적 구매액",
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        plot_bgcolor="white",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(0,0,0,0.06)", zeroline=False)
    st.plotly_chart(fig, use_container_width=True)


def render_cohort_heatmap(retention: pd.DataFrame):
    x_labels = [f"{c}개월차" for c in retention.columns]
    y_labels = [str(p) for p in retention.index]

    text = retention.round(1).astype(str) + "%"
    text = text.where(retention.notna(), "")

    fig = go.Figure(
        data=go.Heatmap(
            z=retention.values,
            x=x_labels,
            y=y_labels,
            text=text.values,
            texttemplate="%{text}",
            textfont=dict(size=12, color="white"),
            colorscale="YlOrRd",
            hoverongaps=False,
            colorbar=dict(title=""),
            xgap=3,
            ygap=3,
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_detail_analysis(start_date, end_date):
    """RFM 산포도 + 코호트 리텐션 히트맵을 나란히 표시 (선택한 기간 데이터만)"""
    users, orders, events = load_data()
    period_orders = orders[(orders["order_date"].dt.date >= start_date) & (orders["order_date"].dt.date <= end_date)]

    if period_orders.empty:
        st.info("선택한 기간에 주문 데이터가 없습니다.")
        return

    rfm = compute_rfm(period_orders)
    retention = compute_cohort_retention(users, period_orders)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**RFM 산포도**")
        render_rfm_scatter(rfm)
    with col2:
        st.markdown("**코호트 리텐션 히트맵**")
        if retention.empty:
            st.info("선택한 기간에는 코호트 리텐션을 계산할 신규 가입자 데이터가 없습니다.")
        else:
            render_cohort_heatmap(retention)


# ---------------------------------------------------------
# 기간 필터 (대시보드와 동일한 버튼형 프리셋)
# ---------------------------------------------------------

DATE_PRESETS_DAYS = {
    "최근 7일": 7,
    "최근 4주": 28,
    "최근 3개월": 90,
    "전체 기간": None,
}


def render_insight_date_filter(events: pd.DataFrame):
    """대시보드(1_Dashboard.py)의 사이드바 필터와 완전히 동일한 위젯.
    key를 "date_preset"/"custom_start_date"/"custom_end_date"로 대시보드와
    똑같이 맞춰서, 두 페이지가 사실상 같은 필터 상태를 공유하도록 한다.
    (대시보드에서 기간을 고르고 이 페이지로 넘어와도 그대로 유지됨)"""
    min_date = events["timestamp"].min().date()
    max_date = events["timestamp"].max().date()

    # 대시보드의 "이 기간으로 AI 인사이트 보기" 버튼으로 넘어온 경우, 대시보드 쪽에서
    # 별도의 carry key에 담아둔 값을 여기서 위젯 key로 옮겨 심는다. Streamlit은
    # st.switch_page로 페이지를 이동하면 위젯에 연결된 session_state를 초기화하기
    # 때문에, key만 같다고 값이 자동으로 유지되지는 않는다 (버튼 클릭 직후 1회만 필요
    # 하므로 pop으로 꺼내 쓰고 지운다).
    for widget_key, carry_key in [
        ("date_preset", "ai_carry_date_preset"),
        ("custom_start_date", "ai_carry_custom_start"),
        ("custom_end_date", "ai_carry_custom_end"),
    ]:
        if widget_key not in st.session_state:
            carried = st.session_state.pop(carry_key, None)
            if carried is not None:
                st.session_state[widget_key] = carried

    with st.sidebar:
        st.markdown("### 기간 필터")
        options = list(DATE_PRESETS_DAYS.keys()) + ["직접 선택"]
        radio_kwargs = {} if "date_preset" in st.session_state else {"index": options.index("전체 기간")}
        choice = st.radio("빠른 선택", options, key="date_preset", **radio_kwargs)

        if choice == "직접 선택":
            available_dates = sorted(events["timestamp"].dt.date.unique())
            start_kwargs = {} if "custom_start_date" in st.session_state else {"index": 0}
            end_kwargs = {} if "custom_end_date" in st.session_state else {"index": len(available_dates) - 1}
            start = st.selectbox("시작일", available_dates, key="custom_start_date", **start_kwargs)
            end = st.selectbox("종료일", available_dates, key="custom_end_date", **end_kwargs)
            if start > end:
                st.warning("시작일이 종료일보다 늦을 수 없습니다.")
                return None, None
        else:
            days_back = DATE_PRESETS_DAYS[choice]
            if days_back:
                start = max(min_date, (pd.Timestamp(max_date) - pd.Timedelta(days=days_back - 1)).date())
            else:
                start = min_date
            end = max_date
        st.caption(f"{start} ~ {end}")

    return start, end


# ---------------------------------------------------------
# 메인 패널 (AI 인사이트 + 상세 분석 통합)
# ---------------------------------------------------------

TAB_BOX_CSS = """
<style>
/* 실제 DOM 구조(개발자도구로 확인): 탭은 <button data-baseweb="tab">이 아니라
   <div data-testid="stTab">이고, 선택 여부는 aria-selected/data-selected로
   표시되며, 밑줄은 .react-aria-SelectionIndicator가 별도로 그린다.
   Streamlit 자체 스타일과 우선순위 경쟁이 있어 전부 !important로 덮어쓴다. */
div[data-testid="stTabs"] [role="tablist"] {
    gap: 8px !important;
    border-bottom: none !important;
    box-shadow: none !important;
    flex-wrap: wrap !important;
}
/* 탭 리스트 바로 아래(패널 위)에 Streamlit이 기본으로 그리는 구분선 제거.
   버전에 따라 border-bottom이 tablist의 부모 wrapper에 붙거나, 별도의
   tab-border/tab-highlight 트랙 엘리먼트로 그려지므로 둘 다 대응한다. */
div[data-testid="stTabs"] {
    border-bottom: none !important;
}
div[data-testid="stTabs"] > div:first-child {
    border-bottom: none !important;
    box-shadow: none !important;
}
div[data-testid="stTabs"] [data-baseweb="tab-border"],
div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
    display: none !important;
}
/* 위 두 셀렉터로도 안 지워지는 경우 대비: tab-list 자체와 그 부모 wrapper에
   남아있는 하단 보더/그림자를 한 번 더 제거한다 (Streamlit 버전에 따라
   div[data-baseweb="tab-border"] 대신 tab-list 요소 자체에 border가 붙기도 함). */
div[data-testid="stTabs"] [data-baseweb="tab-list"] {
    border-bottom: none !important;
    box-shadow: none !important;
}
div[data-testid="stTabs"] [data-baseweb="tabs"] {
    border-bottom: none !important;
}
/* .react-aria-SelectionIndicator를 각 탭(stTab) 안에서만 찾아 숨겼더니 계속
   선이 남아있었다 — 이 라이브러리는 선택 표시용 트랙을 각 탭 내부가 아니라
   tablist 바로 아래에 형제 엘리먼트(전체 너비 바)로 그리는 경우가 있다.
   중첩 위치와 무관하게 stTabs 전체 범위에서 찾아 숨긴다. */
div[data-testid="stTabs"] .react-aria-SelectionIndicator {
    display: none !important;
}
/* role=tablist를 감싸는 임의의 래퍼(클래스명을 모르는 경우 대비)에 남아있는
   테두리/그림자도 :has()로 찾아서 제거한다. */
div[data-testid="stTabs"] div:has(> [role="tablist"]) {
    border-bottom: none !important;
    box-shadow: none !important;
}
/* 여기까지도 계속 선이 남는다는 건 border/box-shadow가 아니라 어떤 요소의
   ::before/::after 가상 요소로 그려지고 있다는 뜻일 가능성이 높다. stTab
   pill 자체를 제외하고, stTabs 하위 모든 요소의 보더·그림자·가상요소를
   통째로 무력화한다 (탭 pill의 테두리는 별도 규칙에서 명시적으로 다시
   그리므로 여기서 지워져도 상관없다). */
div[data-testid="stTabs"] *:not([data-testid="stTab"])::before,
div[data-testid="stTabs"] *:not([data-testid="stTab"])::after {
    display: none !important;
    content: none !important;
}
div[data-testid="stTabs"] *:not([data-testid="stTab"]) {
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
}
/* 얇고 납작한 필(pill) 스타일 탭 */
div[data-testid="stTab"] {
    height: auto !important;
    background-color: #FFFFFF !important;
    border-radius: 8px !important;
    padding: 6px 16px !important;
    border: 1px solid #E2E5E9 !important;
    transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
/* 라벨(이모지+텍스트)이 내부 p 태그 안에 있어 색/굵기는 여기서 지정 */
div[data-testid="stTab"] [data-testid="stMarkdownContainer"] p {
    color: #495057 !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    margin: 0 !important;
}
div[data-testid="stTab"]:hover {
    border-color: #ADB5BD !important;
    background-color: #F8F9FA !important;
}
div[data-testid="stTab"][aria-selected="true"] {
    background-color: #EEF2FF !important;
    border: 1px solid #4C6EF5 !important;
}
div[data-testid="stTab"][aria-selected="true"] [data-testid="stMarkdownContainer"] p {
    color: #4C6EF5 !important;
    font-weight: 700 !important;
}
/* 선택된 탭 아래 남아있던 밑줄 인디케이터 제거 */
div[data-testid="stTab"] .react-aria-SelectionIndicator {
    display: none !important;
}
/* 카드(바깥 박스)의 네 면 여백을 전부 동일하게 맞춘다. 위/아래에만 값을
   줬던 이전 시도는 tabpanel 자체의 padding이 그 위에 별도로 더 쌓이는 데다,
   좌/우는 아예 손대지 않아 기본값 그대로였다 — 그 결과 네 면이 전부 다른
   값이 되고 있었다. 카드 wrapper(어느 깊이에 있든 stVerticalBlock을
   붙잡도록 하위 선택자로 넓힘)에 네 면 동일 padding을 명시하고, tabpanel
   쪽의 아래/좌/우 padding은 0으로 없애서 이중으로 쌓이지 않게 한다. 탭
   pill과 본문 사이 간격(padding-top)만 그대로 남겨 둔다 — 이건 카드
   가장자리 여백이 아니라 탭-본문 사이 내부 간격이라 별개다. */
div[class*="st-key-ai_insight_box"] [data-testid="stVerticalBlock"] {
    padding: 16px !important;
}
div[data-testid="stTabPanel"] {
    padding-top: 16px !important;
    padding-bottom: 0 !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
    border-top: none !important;
}
/* Streamlit은 각 엘리먼트(마크다운 블록 등)마다 기본 margin/gap을 넣어서,
   카드 padding을 아무리 맞춰도 본문 텍스트가 그 안쪽에서 한 번 더 밀려나
   보이는 경우가 있었다. tabpanel 안의 마크다운 wrapper 여백을 0으로 만들어
   본문이 padding 경계에 딱 맞춰 붙도록 한다. */
div[data-testid="stTabPanel"] [data-testid="stElementContainer"],
div[data-testid="stTabPanel"] [data-testid="element-container"],
div[data-testid="stTabPanel"] [data-testid="stMarkdownContainer"] {
    margin: 0 !important;
}
/* "캠페인 자동화 시작하기" 버튼 오른쪽 정렬은 더 이상 CSS로 하지 않는다.
   (예전엔 keyed container + flex/text-align 조합으로 시도했는데, flex
   컨테이너 자체가 버튼 콘텐츠 크기만큼만 잡혀서 밀어낼 공간이 없었고
   실제로는 효과가 없었다. render_ai_panel()의 중첩 st.columns()로
   대체했다 — 자세한 설명은 그쪽 주석 참고.) */
</style>
"""


def render_section_markdown(placeholder, text: str):
    """섹션 본문을 렌더링한다.

    st.markdown()의 기본 마크다운 파서는 한글 문장부호(0.0%, 원, 명 등)
    바로 앞뒤에 **가 붙는 경우 CommonMark의 강조 판별 규칙에 걸려 볼드로
    인식하지 못하고 '**' 글자를 그대로 화면에 보여주는 경우가 있었다.
    이를 확실히 고치기 위해 마크다운 파서에 의존하지 않고, **강조** 구문만
    직접 정규식으로 찾아 <strong> 태그로 변환한 뒤 HTML로 렌더링한다.
    (그 외 텍스트는 HTML 인젝션 방지를 위해 escape 처리)
    """
    escaped = html.escape(text)
    with_bold = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong style='font-weight:700; color:#212529;'>\1</strong>",
        escaped,
        flags=re.S,
    )
    with_breaks = with_bold.replace("\n", "<br>")
    placeholder.markdown(
        f"<div style='line-height:1.8; font-size:15px; color:#343A40;'>{with_breaks}</div>",
        unsafe_allow_html=True,
    )


def render_ai_panel():
    """AI 인사이트 + 상세 분석 패널 - 2_AI_Insights.py에서 호출"""

    if "ai_insight_sections" not in st.session_state:
        st.session_state.ai_insight_sections = None
        st.session_state.ai_insight_time = None
        st.session_state.ai_insight_period = None

    st.markdown(TAB_BOX_CSS, unsafe_allow_html=True)

    header_col1, header_col2 = st.columns([3, 2])
    with header_col1:
        st.subheader("🤖 AI 인사이트")
    with header_col2:
        if st.session_state.ai_insight_time:
            st.markdown(
                f"""
                <div style="text-align: right; margin-top: 22px;">
                    <span style="
                        display: inline-block;
                        white-space: nowrap;
                        background-color: #EEF2FF;
                        color: #4F46E5;
                        padding: 4px 14px;
                        border-radius: 999px;
                        font-size: 13px;
                        font-weight: 500;
                    ">✨ Claude AI · {st.session_state.ai_insight_time}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

    users, orders, events = load_data()
    start_date, end_date = render_insight_date_filter(events)
    if start_date is None:
        return
    current_period = (start_date, end_date)

    # 대시보드의 "이 기간으로 AI 인사이트 보기" 버튼을 눌러서 넘어온 경우,
    # 페이지 전환뿐 아니라 그 자리에서 바로 분석까지 자동 실행한다 (1회성 플래그)
    auto_trigger = st.session_state.pop("ai_auto_trigger", False)

    is_stale = (
        st.session_state.ai_insight_period is not None
        and st.session_state.ai_insight_period != current_period
    )
    if is_stale and not auto_trigger:
        st.info("기간이 변경됐습니다. 새 기간으로 다시 분석하려면 아래 버튼을 눌러주세요. (이전 결과가 표시되고 있어요)")

    has_result = st.session_state.ai_insight_sections is not None

    # 헤더의 "✨ Claude AI · 분석시각" pill과 오른쪽 끝이 맞도록, 헤더와
    # 같은 [3, 2] 비율을 쓴다. (예전엔 [1, 1] + CSS flex-end로 오른쪽 정렬을
    # 시도했는데, flex 컨테이너 자체가 버튼 콘텐츠 크기만큼만 잡혀서 밀어낼
    # 공간이 없었고, 결과적으로 버튼이 그냥 왼쪽 버튼 옆에 붙어버렸다.
    # CSS 대신 Streamlit 컬럼이 항상 지정한 비율만큼 폭을 차지한다는 점을
    # 이용해 중첩 컬럼으로 오른쪽 정렬을 구현한다.)
    btn_col1, btn_col2 = st.columns([3, 2])
    with btn_col1:
        analyze_clicked = st.button(
            "🔄 다시 분석하기" if has_result else "✨ 분석하기",
            type="primary",
        )
    analyze_clicked = analyze_clicked or auto_trigger

    with btn_col2:
        # "캠페인 자동화 시작하기" 버튼을 실행 권장사항 탭 안이 아니라
        # 분석/다시 분석하기 버튼 바로 옆으로 이동. 아직 분석 결과가 없으면
        # 캠페인을 시작할 대상 세그먼트도 없으므로 버튼 자체를 숨긴다.
        if has_result:
            # 왼쪽에 빈 스페이서 컬럼을 두고, 버튼은 오른쪽 서브컬럼 안에서
            # use_container_width=True로 꽉 채운다. 서브컬럼의 오른쪽 끝은
            # 항상 btn_col2(=header_col2)의 오른쪽 끝과 정확히 일치하므로,
            # 스페이서 비율이 조금 안 맞아도 "오른쪽 정렬" 자체는 항상 보장된다.
            _spacer_col, campaign_btn_col = st.columns([1, 3])
            with campaign_btn_col:
                if st.button(
                    "🚀 캠페인 자동화 시작하기",
                    type="secondary",
                    key="campaign_start_btn",
                    use_container_width=True,
                ):
                    sections_for_campaign = st.session_state.ai_insight_sections
                    # AI가 리포트에서 실제로 언급한 세그먼트를 최우선으로 쓴다.
                    # (리포트 내용과 캠페인 대상이 어긋나지 않도록) 못 읽었을 때만
                    # 규칙 기반 추정(recommend_segment)으로 대체한다.
                    target_segment = extract_target_segment(sections_for_campaign) or recommend_segment(
                        start_date, end_date, users, orders, events
                    )
                    st.session_state["recommended_segment"] = target_segment
                    st.switch_page("pages/2_Campaign.py")

    with st.container(border=True, key="ai_insight_box"):
        # 탭을 먼저 만들어두고, 분석이 진행되는 동안 이 탭 안의 자리에 텍스트를 그대로 채워 넣는다
        # (분석이 끝난 뒤에 줄글 → 탭으로 바뀌는 게 아니라, 스트리밍 시작부터 탭 형태 유지)
        tabs = st.tabs(TAB_LABELS)
        placeholders = []
        for tab in tabs:
            with tab:
                placeholders.append(st.empty())

        if analyze_clicked:
            summary = get_dashboard_summary(start_date, end_date)
            full_response = ""
            for ph in placeholders:
                ph.markdown("_분석 중입니다..._")
            analysis_failed = False
            try:
                for chunk in run_ai_analysis(summary):
                    full_response += chunk
                    sections = parse_ai_sections(full_response)
                    for i, keyword in enumerate(TAB_KEYWORDS):
                        content = find_section(sections, keyword)
                        if content:
                            render_section_markdown(placeholders[i], content)
            except Exception as e:
                analysis_failed = True
                st.warning(f"분석 중 연결이 끊겼습니다 ({e}). '다시 분석하기'를 눌러 재시도해주세요.")

            # 연결이 끊겼거나 응답이 비어 있으면(예: 빈 텍스트만 오고 예외 없이 끝난 경우)
            # 실패로 간주하고 session_state를 덮어쓰지 않는다. 여기서 그대로 저장 +
            # rerun을 했더니, 실패해도 has_result가 True가 돼서 경고 메시지는 rerun과
            # 함께 화면에서 사라지고 캠페인 버튼은 빈 결과를 대상으로 노출되는 문제가
            # 있었다. 이전에 성공한 분석 결과가 있었다면(재시도 실패 케이스) 그것도
            # 그대로 보존된다 — 실패한 재시도가 유효했던 이전 리포트를 지워버리지 않는다.
            if not analysis_failed and full_response.strip():
                st.session_state.ai_insight_sections = parse_ai_sections(full_response)
                st.session_state.ai_insight_time = datetime.now().strftime("%H:%M 분석")
                st.session_state.ai_insight_period = current_period
                st.rerun()
            elif not analysis_failed:
                # 예외는 안 났지만 응답이 비어 있는 경우 (예: 스트림이 아무 텍스트도 없이 끝남)
                st.warning("분석 결과가 비어 있습니다. '다시 분석하기'를 눌러 재시도해주세요.")

        elif has_result:
            sections = st.session_state.ai_insight_sections
            for i, keyword in enumerate(TAB_KEYWORDS):
                content = find_section(sections, keyword)
                if content:
                    render_section_markdown(placeholders[i], content)
                else:
                    placeholders[i].markdown("_해당 섹션 내용이 없습니다._")
        else:
            for ph in placeholders:
                ph.markdown("_상단의 '분석하기' 버튼을 눌러 이 기간의 인사이트를 생성해주세요._")

    with st.expander("📊 상세 분석 (RFM 산포도 · 코호트 리텐션)", expanded=False):
        render_detail_analysis(start_date, end_date)