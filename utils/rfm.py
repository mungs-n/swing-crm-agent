"""
RFM 계산 유틸리티
charts.py에서 불러다 씁니다
"""

import pandas as pd
from datetime import datetime


def calculate_rfm(orders: pd.DataFrame) -> pd.DataFrame:
    """
    RFM 계산
    - Recency: 마지막 구매일로부터 경과 일수
    - Frequency: 총 구매 횟수
    - Monetary: 총 구매 금액
    """
    today = datetime.today()
    orders["order_date"] = pd.to_datetime(orders["order_date"])

    rfm = orders.groupby("user_id").agg(
        Recency=("order_date", lambda x: (today - x.max()).days),
        Frequency=("order_id", "count"),
        Monetary=("total_amount", "sum")
    ).reset_index()

    return rfm


def assign_segment(rfm: pd.DataFrame) -> pd.DataFrame:
    """RFM 점수 기반 세그먼트 분류"""
    rfm["R_score"] = pd.qcut(rfm["Recency"], q=4, labels=[4, 3, 2, 1])
    rfm["F_score"] = pd.qcut(rfm["Frequency"].rank(method="first"), q=4, labels=[1, 2, 3, 4])
    rfm["M_score"] = pd.qcut(rfm["Monetary"], q=4, labels=[1, 2, 3, 4])

    rfm["RFM_score"] = (
        rfm["R_score"].astype(int) +
        rfm["F_score"].astype(int) +
        rfm["M_score"].astype(int)
    )

    def label(score):
        if score >= 10:
            return "VIP"
        elif score >= 8:
            return "충성 고객"
        elif score >= 6:
            return "이탈 위험"
        else:
            return "휴면"

    rfm["segment"] = rfm["RFM_score"].apply(label)
    return rfm
