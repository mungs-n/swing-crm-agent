"""
Supabase에서 데이터를 읽어오는 공용 로더.
dataset_source별로 통화 표시 방식이 달라서(ATHLEPA=원화, 데이콘=달러),
CURRENCY 설정도 여기서 같이 관리한다.
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import streamlit as st
from supabase import create_client

DATASET_OPTIONS = {
    "athlepa": "ATHLEPA (자체 데이터)",
    "dacon": "데이콘 이커머스 공개 데이터",
}

# 데이터셋에 해당 테이블 행이 아예 없을 때도(예: 데이콘의 events) 코드가 컬럼이
# 없다고 에러내지 않도록, 빈 DataFrame이라도 스키마상 컬럼은 갖추게 한다.
TABLE_COLUMNS = {
    "users": ["dataset_source", "user_id", "name", "persona_type", "age", "gender", "region", "acquisition_channel", "signup_date"],
    "orders": ["dataset_source", "order_id", "user_id", "order_date", "total_amount", "discount_amount", "coupon_used", "category"],
    "events": ["dataset_source", "user_id", "session_id", "timestamp", "event_type", "product_id", "category", "price"],
}

CURRENCY = {
    "athlepa": {"symbol": "₩", "scale": 1_000_000, "scale_label": "M"},
    "dacon": {"symbol": "$", "scale": 1_000, "scale_label": "K"},
}


def get_dataset_source() -> str:
    return st.session_state.get("dataset_source", "athlepa")


def currency_config(dataset_source: str | None = None) -> dict:
    return CURRENCY[dataset_source or get_dataset_source()]


def fmt_amount(value: float, dataset_source: str | None = None, scaled: bool = True) -> str:
    """금액을 현재 선택된 데이터셋의 통화 기호/단위로 포맷."""
    cfg = currency_config(dataset_source)
    if scaled:
        return f"{cfg['symbol']}{value / cfg['scale']:.1f}{cfg['scale_label']}"
    return f"{cfg['symbol']}{value:,.0f}"


_thread_local = threading.local()

# 프로세스 전체에서 하나만 만들어서 계속 재사용하는 스레드풀. _fetch_all을 호출할 때마다
# `with ThreadPoolExecutor(...) as pool`로 새로 만들면, 그때마다 워커 스레드가 새로 뜨면서
# 스레드별 클라이언트(TLS 연결)도 매번 새로 맺어야 해서 재사용 효과가 없었다. 이 풀을
# 앱이 켜져 있는 동안 계속 재사용하면 두 번째 호출부터는 이미 연결된 스레드를 그대로 쓴다.
_executor = ThreadPoolExecutor(max_workers=30)


def _get_client():
    """스레드마다 클라이언트를 하나씩 재사용한다. supabase-py 내부 httpx.Client를 여러
    스레드가 동시에 공유하면 요청이 멈추는 경우가 있어서 스레드 간 공유는 피하되,
    호출할 때마다 새로 만들면 TLS 연결을 매번 새로 맺어야 해서 느려지므로
    스레드별로는 재사용한다."""
    client = getattr(_thread_local, "client", None)
    if client is None:
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
        _thread_local.client = client
    return client


def _fetch_all(
    table: str,
    dataset_source: str | None,
    columns: str = "*",
    date_column: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """Supabase(PostgREST)는 프로젝트 설정상 한 번에 최대 1000행만 돌려준다(range를 더 크게
    요청해도 잘림). 133K행짜리 events 같은 테이블을 순차 페이지네이션하면 요청이 수백 번
    걸려 느려지므로, 먼저 전체 건수를 세고 필요한 페이지를 스레드풀로 동시에 가져온다.

    date_column/date_from/date_to를 주면 그 기간만 필터링해서 가져온다 — 전체 이력이 아니라
    최근 며칠치만 필요한 화면(예: 홈 화면의 최근 활성 고객 수)에서 컬럼도 좁혀서 쓰면
    훨씬 적은 데이터만 오가서 빨라진다."""
    page_size = 1000

    def apply_filters(q):
        if dataset_source is not None:
            q = q.eq("dataset_source", dataset_source)
        if date_column and date_from:
            q = q.gte(date_column, date_from)
        if date_column and date_to:
            q = q.lte(date_column, date_to)
        return q

    # count="estimated"(통계 기반 추정치)가 더 빠르긴 한데, 방금 대량으로 밀어넣은 테이블은
    # 통계가 아직 안 갱신돼서 추정치가 실제 행 수보다 작게 나올 수 있고, 그러면 뒤쪽 데이터가
    # 통째로 누락된다. 속도보다 정확한 데이터가 더 중요해서 exact를 그대로 쓴다.
    base = apply_filters(_get_client().table(table).select("*", count="exact"))
    count_res = base.limit(1).execute()
    total = count_res.count or 0
    if total == 0:
        return pd.DataFrame(columns=TABLE_COLUMNS.get(table, []))

    def fetch_page(start: int) -> list[dict]:
        q = apply_filters(_get_client().table(table).select(columns)).range(start, start + page_size - 1)
        return q.execute().data

    starts = list(range(0, total, page_size))
    pages = list(_executor.map(fetch_page, starts))

    rows = [row for page in pages for row in page]
    return pd.DataFrame(rows)


@st.cache_data(ttl=60, show_spinner="데이터를 불러오는 중...")
def load_data(dataset_source: str = "athlepa"):
    """대시보드가 쓰는 users/orders/events를 Supabase에서 읽어온다.
    ttl=60이라 1분마다 최신 데이터를 다시 가져온다 (정적 CSV 캐시와의 차이점)."""
    users = _fetch_all("users", dataset_source)
    orders = _fetch_all("orders", dataset_source)
    events = _fetch_all("events", dataset_source)

    # 빈 DataFrame이어도 pd.to_datetime은 정상 동작하므로 굳이 empty 체크로 건너뛰지 않는다.
    # 건너뛰면 컬럼이 object dtype인 채로 남아서, 나중에 .dt 접근자를 쓰는 코드가 깨진다.
    if "signup_date" in users.columns:
        users["signup_date"] = pd.to_datetime(users["signup_date"])
    if "order_date" in orders.columns:
        orders["order_date"] = pd.to_datetime(orders["order_date"])
    if "timestamp" in events.columns:
        events["timestamp"] = pd.to_datetime(events["timestamp"])

    return users, orders, events


@st.cache_data(ttl=60, show_spinner="데이터를 불러오는 중...")
def load_users_orders(dataset_source: str = "athlepa"):
    """홈 화면처럼 events 전체가 필요 없는 곳에서 쓰는 가벼운 버전.
    users/orders만 가져온다 (13만 행짜리 events는 건드리지 않음)."""
    users = _fetch_all("users", dataset_source)
    orders = _fetch_all("orders", dataset_source)
    if "signup_date" in users.columns:
        users["signup_date"] = pd.to_datetime(users["signup_date"])
    if "order_date" in orders.columns:
        orders["order_date"] = pd.to_datetime(orders["order_date"])
    return users, orders


@st.cache_data(ttl=60, show_spinner="데이터를 불러오는 중...")
def load_recent_active_users(dataset_source: str, since: str, until: str) -> pd.DataFrame:
    """홈 화면의 '최근 30일 활성 고객' 계산 전용. 전체 events(13만 행, 8컬럼)를 다 끌어오지
    않고, 필요한 기간(user_id, timestamp 두 컬럼)만 좁혀서 가져온다."""
    df = _fetch_all(
        "events", dataset_source, columns="user_id,timestamp",
        date_column="timestamp", date_from=since, date_to=until,
    )
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df
