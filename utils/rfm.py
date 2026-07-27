"""
RFM 계산 유틸리티
charts.py에서 불러다 씁니다
"""

import pandas as pd


def calculate_rfm(orders: pd.DataFrame) -> pd.DataFrame:
    """
    RFM 계산
    - Recency: 마지막 구매일로부터 경과 일수 (데이터셋 내 최신 주문일 기준)
    - Frequency: 총 구매 횟수
    - Monetary: 총 구매 금액
    """
    orders["order_date"] = pd.to_datetime(orders["order_date"])
    as_of = orders["order_date"].max()

    rfm = orders.groupby("user_id").agg(
        Recency=("order_date", lambda x: (as_of - x.max()).days),
        Frequency=("order_id", "count"),
        Monetary=("total_amount", "sum")
    ).reset_index()

    return rfm


def assign_segment(rfm: pd.DataFrame) -> pd.DataFrame:
    """RFM 점수 기반 세그먼트 분류"""
    if len(rfm) < 4:
        rfm["segment"] = "표본 부족"
        return rfm

    # rank로 먼저 순위를 매겨 동일값(구간이 좁아 Recency가 전부 0인 경우 등)이 많아도
    # qcut의 구간 경계가 항상 유일하도록 처리
    rfm["R_score"] = pd.qcut(rfm["Recency"].rank(method="first"), q=4, labels=[4, 3, 2, 1])
    rfm["F_score"] = pd.qcut(rfm["Frequency"].rank(method="first"), q=4, labels=[1, 2, 3, 4])
    rfm["M_score"] = pd.qcut(rfm["Monetary"].rank(method="first"), q=4, labels=[1, 2, 3, 4])

    rfm["RFM_score"] = (
        rfm["R_score"].astype(int) +
        rfm["F_score"].astype(int) +
        rfm["M_score"].astype(int)
    )

    # 예전에는 RFM_score(3~12점)를 고정 구간(10점 이상=VIP 등)으로 잘랐는데, 실제
    # 데이터에서는 R/F/M이 서로 독립이 아니라 강하게 상관돼 있어서(자주 사는 사람이
    # 많이도 쓰고 최근에도 삼) 점수가 양 극단(3~5점, 10~12점)에 쏠리고, 그 결과 VIP가
    # 항상 가장 큰 집단이 되는 등 "각 등급 25%씩"이라는 원래 의도가 깨졌다. 고정
    # 구간 대신 RFM_score 자체를 4분위로 다시 나눠서, 상관관계와 무관하게 네 등급이
    # 항상 균등하게(약 25%씩) 나뉘도록 한다.
    rfm["segment"] = pd.qcut(
        rfm["RFM_score"].rank(method="first"),
        q=4,
        labels=["휴면", "이탈 위험", "충성 고객", "VIP"],
    )
    return rfm
