"""
A/B 테스트 데이터 로딩 및 통계 계산
담당: 가연

campaign_sends는 SendGrid Event Webhook(ingestion_server의 /sendgrid-events)이 opened_at/
clicked_at을 실시간으로 채워주는 Supabase 테이블이라, 여기서도 CSV 대신 Supabase에서 바로
읽는다 (campaign_history/campaign_sends는 회사 계정 구분 없이 항상 'athlepa' 고정이라
dataset_source 필터는 필요 없다 - data/schema.sql 참고).
"""

import math
import os

import pandas as pd
import streamlit as st
from supabase import create_client

AB_TESTS_COLUMNS = [
    "test_id", "test_name", "segment", "channel", "success_metric", "status",
    "created_at", "ended_at", "winner_group_id",
    "group_id", "group_label", "is_control", "users", "conversions", "clicks", "opens",
]

CAMPAIGN_SENDS_COLUMNS = [
    "send_id", "campaign_id", "user_id", "segment", "channel", "sent_at", "delivered",
    "opened_at", "clicked_at", "converted_order_id", "conversion_type", "revenue",
]

_PAGE_SIZE = 1000  # Supabase(PostgREST)가 한 번에 최대 1000행만 돌려줌


def _get_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


@st.cache_data(ttl=30, show_spinner="발송 데이터를 불러오는 중...")
def load_campaign_sends() -> pd.DataFrame:
    """campaign_sends 테이블 전체를 Supabase에서 읽어온다. ttl=30이라 30초마다
    최신 오픈/클릭 데이터로 다시 불러온다 (SendGrid 웹훅으로 실시간 업데이트되는 값 반영용)."""
    client = _get_client()
    rows = []
    start = 0
    while True:
        page = client.table("campaign_sends").select("*").order("id").range(start, start + _PAGE_SIZE - 1).execute().data
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE

    if not rows:
        return pd.DataFrame(columns=CAMPAIGN_SENDS_COLUMNS)

    df = pd.DataFrame(rows)
    # format="ISO8601": 합성 데이터(초 단위)와 실시간 발송(마이크로초 단위)의 정밀도가
    # 달라서, format을 지정하지 않으면 pandas가 앞부분 값으로 추론한 형식이 뒤에 나오는
    # 다른 정밀도의 값에서 깨져 NaT가 되고, 그러면 날짜 필터에서 통째로 빠진다.
    for col in ["sent_at", "opened_at", "clicked_at"]:
        df[col] = pd.to_datetime(df[col], errors="coerce", format="ISO8601")
    df["delivered"] = df["delivered"].astype(bool)
    return df


@st.cache_data
def load_ab_tests() -> pd.DataFrame:
    """A/B 테스트 그룹별 집계 데이터 (테스트 1개 = 여러 행, 그룹별로 1행씩).
    아직 테스트를 하나도 만들지 않아 data/ab_tests.csv가 없는 초기 상태에서도
    화면이 죽지 않도록 빈 DataFrame(스키마는 유지)을 돌려준다."""
    try:
        df = pd.read_csv("data/ab_tests.csv")
    except FileNotFoundError:
        return pd.DataFrame(columns=AB_TESTS_COLUMNS)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["ended_at"] = pd.to_datetime(df["ended_at"], errors="coerce")
    df["is_control"] = df["is_control"].astype(bool)
    return df


def save_ab_test(test_row: dict, groups: list[dict]) -> None:
    """새 A/B 테스트 생성 시 data/ab_tests.csv에 이어붙임 (그룹 수만큼 행 추가)"""
    existing = load_ab_tests()
    load_ab_tests.clear()  # 캐시 무효화

    rows = []
    for g in groups:
        rows.append({**test_row, **g})
    new_df = pd.DataFrame(rows)

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_csv("data/ab_tests.csv", index=False)


def cvr(users: int, conversions: int) -> float:
    """전환율(%). users가 0이면 0"""
    if not users:
        return 0.0
    return conversions / users * 100


def two_proportion_test(users_a: int, conv_a: int, users_b: int, conv_b: int):
    """A(기준/컨트롤) 대비 B의 전환율 개선율(%), 95% 신뢰구간(%p), p-value를 계산.

    표본 수가 충분하지 않으면(정규근사 조건 미충족) 통계 검정 없이 None들을 반환한다.
    scipy 의존성을 늘리지 않으려고 math.erf로 정규분포 누적확률을 직접 계산했다.
    """
    if users_a == 0 or users_b == 0:
        return None, None, None

    p_a = conv_a / users_a
    p_b = conv_b / users_b

    # 정규근사 최소 조건 (각 그룹 기대 성공/실패 수 >= 5)
    if min(conv_a, users_a - conv_a, conv_b, users_b - conv_b) < 5:
        uplift = ((p_b - p_a) / p_a * 100) if p_a > 0 else None
        return uplift, None, None

    # 신뢰구간(차이, unpooled SE)
    se_diff = math.sqrt(p_a * (1 - p_a) / users_a + p_b * (1 - p_b) / users_b)
    diff = p_b - p_a
    ci_low_pp = (diff - 1.96 * se_diff) * 100
    ci_high_pp = (diff + 1.96 * se_diff) * 100

    # 유의성 검정(pooled SE)
    p_pool = (conv_a + conv_b) / (users_a + users_b)
    se_pool = math.sqrt(p_pool * (1 - p_pool) * (1 / users_a + 1 / users_b))
    z = diff / se_pool if se_pool > 0 else 0
    p_value = 2 * (1 - _norm_cdf(abs(z)))

    uplift = (diff / p_a * 100) if p_a > 0 else None
    return uplift, (ci_low_pp, ci_high_pp), p_value


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def test_summary_counts() -> dict:
    """상단 KPI 요약용: 진행중/완료/통계적으로 유의미한 테스트 수."""
    ab_df = load_ab_tests()
    if ab_df.empty:
        return {"running": 0, "done": 0, "significant": 0}

    running = ab_df.drop_duplicates("test_id").query("status == '진행중'").shape[0]
    done = ab_df.drop_duplicates("test_id").query("status == '완료'").shape[0]

    significant = 0
    for test_id, test_df in ab_df.groupby("test_id"):
        control = test_df[test_df["is_control"]]
        baseline = control.iloc[0] if not control.empty else test_df.iloc[0]
        is_sig = False
        for _, g in test_df.iterrows():
            if g["group_id"] == baseline["group_id"]:
                continue
            _, _, p_value = two_proportion_test(
                int(baseline["users"]), int(baseline["conversions"]), int(g["users"]), int(g["conversions"])
            )
            if p_value is not None and p_value < 0.05:
                is_sig = True
                break
        if is_sig:
            significant += 1

    return {"running": running, "done": done, "significant": significant}
