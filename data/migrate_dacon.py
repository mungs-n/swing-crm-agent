"""
데이콘 '이커머스 고객 세분화' 공개 데이터셋을 ATHLEPA CRM 스키마(dataset_source='dacon')로
매핑해서 Supabase에 적재하는 스크립트.

원본 파일(Customer_info.csv, Onlinesales_info.csv, Discount_info.csv)은
data/dacon_raw/ 에 있어야 한다 (dacon.io에서 직접 받아야 하는 파일이라 git에는 올리지 않음).

매핑 시 주의할 점:
- Onlinesales_info.csv의 거래ID는 여러 고객이 공유하는 경우가 있어(원본 데이터 특성),
  (거래ID, 고객ID) 조합을 하나의 주문 단위로 묶어서 order_id를 만든다.
- 금액은 원본이 USD 기준이라 원화(KRW) ATHLEPA 데이터와 단순 합산하면 안 된다.
  대시보드에서 데이터셋을 하나씩 선택해서 보는 구조라 당장은 변환하지 않고 USD 그대로 둔다.
- users.csv에 없는 name/persona_type/age/acquisition_channel은 null로 남긴다.
- 이 데이터셋은 개별 방문/장바구니 같은 행동 로그가 없어 events 테이블은 채우지 않는다
  (퍼널/행동 분석 탭은 이 데이터셋 선택 시 데이터 없음으로 표시됨).
"""

import math
import os

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

RAW_DIR = "data/dacon_raw"
BATCH_SIZE = 500
SOURCE = "dacon"

MONTH_MAP = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def get_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


def clean(value):
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def clean_records(df: pd.DataFrame) -> list[dict]:
    records = df.to_dict(orient="records")
    return [{k: clean(v) for k, v in r.items()} for r in records]


def upsert_batches(sb, table, records, on_conflict):
    total = len(records)
    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        sb.table(table).upsert(batch, on_conflict=on_conflict).execute()
        print(f"  {table}: {min(i + BATCH_SIZE, total)}/{total}")


def migrate_users(sb):
    cust = pd.read_csv(f"{RAW_DIR}/Customer_info.csv", encoding="utf-8-sig")
    sales = pd.read_csv(f"{RAW_DIR}/Onlinesales_info.csv", encoding="utf-8-sig")
    ref_date = pd.to_datetime(sales["거래날짜"]).max()

    cust["dataset_source"] = SOURCE
    cust["signup_date"] = (
        ref_date - pd.to_timedelta(cust["가입기간"] * 30, unit="D")
    ).dt.strftime("%Y-%m-%d")

    df = cust.rename(columns={"고객ID": "user_id", "성별": "gender", "고객지역": "region"})
    for col in ["name", "persona_type", "age", "acquisition_channel"]:
        df[col] = None
    cols = ["dataset_source", "user_id", "name", "persona_type", "age", "gender", "region", "acquisition_channel", "signup_date"]
    upsert_batches(sb, "users", clean_records(df[cols]), on_conflict="dataset_source,user_id")


def migrate_orders(sb):
    sales = pd.read_csv(f"{RAW_DIR}/Onlinesales_info.csv", encoding="utf-8-sig")
    discount = pd.read_csv(f"{RAW_DIR}/Discount_info.csv", encoding="utf-8-sig")

    sales["거래날짜"] = pd.to_datetime(sales["거래날짜"])
    sales["line_amount"] = sales["평균금액"] * sales["수량"]
    sales["월"] = sales["거래날짜"].dt.month.map(MONTH_MAP)

    # (거래ID, 고객ID) 조합을 주문 1건으로 취급 (원본 거래ID가 여러 고객에 걸쳐 재사용됨)
    grouped = sales.groupby(["거래ID", "고객ID"]).agg(
        order_date=("거래날짜", "first"),
        line_total=("line_amount", "sum"),
        shipping=("배송료", "first"),
        category=("제품카테고리", lambda s: s.mode().iloc[0]),
        coupon_used=("쿠폰상태", lambda s: (s == "Used").any()),
    ).reset_index()
    grouped["total_amount"] = (grouped["line_total"] + grouped["shipping"]).round(2)
    grouped["order_id"] = grouped["거래ID"] + "_" + grouped["고객ID"]

    # 쿠폰 사용 주문만 (월, 카테고리) 기준 할인율로 할인금액 근사 계산
    grouped = grouped.merge(
        discount.rename(columns={"월": "month_key", "제품카테고리": "category"}),
        left_on=[grouped["order_date"].dt.month.map(MONTH_MAP), "category"],
        right_on=["month_key", "category"],
        how="left",
    )
    grouped["discount_amount"] = 0.0
    mask = grouped["coupon_used"] & grouped["할인율"].notna()
    grouped.loc[mask, "discount_amount"] = (
        grouped.loc[mask, "total_amount"] * grouped.loc[mask, "할인율"] / 100
    ).round(2)

    df = grouped.rename(columns={"고객ID": "user_id"})
    df["dataset_source"] = SOURCE
    cols = ["dataset_source", "order_id", "user_id", "order_date", "total_amount", "discount_amount", "coupon_used", "category"]
    upsert_batches(sb, "orders", clean_records(df[cols]), on_conflict="dataset_source,order_id")
    print(f"  (원본 거래 라인 {len(sales)}건 -> 주문 {len(df)}건으로 집계)")


def main():
    sb = get_client()
    print("dacon users...")
    migrate_users(sb)
    print("dacon orders...")
    migrate_orders(sb)
    print("done")


if __name__ == "__main__":
    main()
