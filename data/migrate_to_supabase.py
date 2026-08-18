"""
기존 CSV(data/*.csv)를 Supabase(schema.sql로 만든 테이블)에 적재하는 마이그레이션 스크립트.
users/orders/events는 dataset_source='athlepa'로 채워서 넣는다.
"""

import math
import os

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

BATCH_SIZE = 500


def get_client():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def clean(value):
    """NaN/NaT -> None, numpy/pandas 타입 -> 순수 파이썬 타입, Timestamp -> ISO 문자열.

    NaN이 섞인 정수 컬럼(revenue, price 등)은 pandas가 통째로 float64로 승격시켜서
    120799 같은 값이 120799.0으로 들어온다. DataFrame 컬럼에 다시 int/None을 섞어
    넣어봐도 pandas가 또 float64로 되돌리므로, 레코드(dict)로 바뀐 뒤 여기서
    정수값을 가진 float만 int로 되돌린다.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def clean_records(df: pd.DataFrame) -> list[dict]:
    records = df.to_dict(orient="records")
    return [{k: clean(v) for k, v in r.items()} for r in records]


def upsert_batches(sb, table, records, on_conflict=None):
    total = len(records)
    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        query = sb.table(table)
        if on_conflict:
            query.upsert(batch, on_conflict=on_conflict).execute()
        else:
            query.insert(batch).execute()
        print(f"  {table}: {min(i + BATCH_SIZE, total)}/{total}")


def migrate_users(sb):
    df = pd.read_csv("data/users.csv", encoding="utf-8-sig")
    df["dataset_source"] = "athlepa"
    df = df.rename(columns={"user_id": "user_id"})
    cols = ["dataset_source", "user_id", "name", "persona_type", "age", "gender", "acquisition_channel", "signup_date"]
    upsert_batches(sb, "users", clean_records(df[cols]), on_conflict="dataset_source,user_id")


def migrate_orders(sb):
    df = pd.read_csv("data/orders.csv", encoding="utf-8-sig")
    df["dataset_source"] = "athlepa"
    cols = ["dataset_source", "order_id", "user_id", "order_date", "total_amount", "discount_amount", "coupon_used", "category"]
    upsert_batches(sb, "orders", clean_records(df[cols]), on_conflict="dataset_source,order_id")


def migrate_events(sb):
    df = pd.read_csv("data/events.csv", encoding="utf-8-sig")
    df["dataset_source"] = "athlepa"
    cols = ["dataset_source", "user_id", "session_id", "timestamp", "event_type", "product_id", "category", "price"]
    upsert_batches(sb, "events", clean_records(df[cols]))


def migrate_campaign_history(sb):
    df = pd.read_csv("data/campaign_history.csv", encoding="utf-8-sig")
    df = df.rename(
        columns={
            "발송일시": "sent_at",
            "세그먼트": "segment",
            "대상 인원": "target_count",
            "메시지 요약": "message_summary",
            "상태": "status",
        }
    )
    cols = ["campaign_id", "sent_at", "segment", "target_count", "message_summary", "status", "approval_mode"]
    upsert_batches(sb, "campaign_history", clean_records(df[cols]), on_conflict="campaign_id")


def migrate_campaign_sends(sb):
    df = pd.read_csv("data/campaign_sends.csv", encoding="utf-8-sig")
    cols = [
        "send_id", "campaign_id", "user_id", "segment", "channel", "sent_at", "delivered",
        "opened_at", "clicked_at", "converted_order_id", "conversion_type", "revenue",
    ]
    upsert_batches(sb, "campaign_sends", clean_records(df[cols]), on_conflict="send_id")


def main():
    sb = get_client()
    print("users...")
    migrate_users(sb)
    print("orders...")
    migrate_orders(sb)
    print("campaign_history...")
    migrate_campaign_history(sb)
    print("campaign_sends...")
    migrate_campaign_sends(sb)
    print("events... (가장 오래 걸림)")
    migrate_events(sb)
    print("done")


if __name__ == "__main__":
    main()
