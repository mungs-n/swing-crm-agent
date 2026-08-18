"""
실시간 트래킹 이벤트 수신 서버.
회사 웹사이트에 심는 tracking.js가 여기로 이벤트를 보내면,
api_key로 어느 회사(dataset_source)인지 찾아서 Supabase events 테이블에 바로 적재한다.
Streamlit 앱과는 별도로 Render에 독립 배포한다 (트래킹 요청이 대시보드 로딩과 서로 영향 주지 않도록).
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client

load_dotenv()

app = FastAPI(title="ATHLEPA CRM Tracking Server")

# 트래킹 스크립트는 고객사 웹사이트(다른 도메인)에서 이 서버로 요청을 보내므로 전체 origin 허용.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/demo", StaticFiles(directory=STATIC_DIR, html=True), name="demo")

_client = None


def get_client():
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    return _client


# 회사 수가 적고 키가 자주 바뀌지 않으므로 메모리에 캐시해서 매 요청마다
# companies 테이블을 조회하지 않게 한다.
_company_cache: dict[str, str] = {}
_webhook_cache: dict[str, str] = {}


def resolve_dataset_source(api_key: str) -> str | None:
    if api_key in _company_cache:
        return _company_cache[api_key]
    res = get_client().table("companies").select("dataset_source").eq("api_key", api_key).limit(1).execute()
    if not res.data:
        return None
    dataset_source = res.data[0]["dataset_source"]
    _company_cache[api_key] = dataset_source
    return dataset_source


def resolve_dataset_source_by_webhook(webhook_secret: str) -> str | None:
    """주문 웹훅 전용 인증. api_key(브라우저 JS에 노출)와는 별개의 키를 쓴다 —
    같은 키를 쓰면 페이지 소스만 보고도 누구나 가짜 매출을 조작해 보낼 수 있기 때문."""
    if webhook_secret in _webhook_cache:
        return _webhook_cache[webhook_secret]
    res = (
        get_client()
        .table("companies")
        .select("dataset_source")
        .eq("webhook_secret", webhook_secret)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    dataset_source = res.data[0]["dataset_source"]
    _webhook_cache[webhook_secret] = dataset_source
    return dataset_source


class TrackEvent(BaseModel):
    api_key: str
    event_type: str
    user_id: str
    session_id: str
    product_id: str | None = None
    category: str | None = None
    price: float | None = None


class OrderWebhook(BaseModel):
    webhook_secret: str
    order_id: str
    user_id: str
    total_amount: float
    order_date: str | None = None  # 없으면 수신 시각으로 채운다
    discount_amount: float = 0
    coupon_used: bool | None = None
    category: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/track")
def track(event: TrackEvent):
    dataset_source = resolve_dataset_source(event.api_key)
    if dataset_source is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 api_key")

    row = {
        "dataset_source": dataset_source,
        "user_id": event.user_id,
        "session_id": event.session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event.event_type,
        "product_id": event.product_id,
        "category": event.category,
        # events.price는 int 컬럼이라 float(예: 39000.0)을 그대로 보내면
        # "invalid input syntax for type integer" 에러가 난다.
        "price": int(event.price) if event.price is not None else None,
    }
    try:
        get_client().table("events").insert(row).execute()
    except Exception as e:
        print(f"[track] insert 실패: {e}")
        raise HTTPException(status_code=500, detail="이벤트 저장에 실패했습니다")
    return {"ok": True}


@app.post("/orders")
def create_order(order: OrderWebhook):
    """회사 백엔드(또는 카페24 등 커머스 플랫폼의 주문완료 웹훅)에서 호출하는 엔드포인트.
    webhook_secret으로 인증하며, 브라우저 JS에 노출되는 api_key와는 별개의 키를 쓴다."""
    dataset_source = resolve_dataset_source_by_webhook(order.webhook_secret)
    if dataset_source is None:
        raise HTTPException(status_code=401, detail="유효하지 않은 webhook_secret")

    # orders.user_id는 users(dataset_source, user_id)를 참조하는 외래키가 걸려 있어서,
    # 처음 주문하는 고객이면 최소 프로필을 먼저 만들어둔다.
    try:
        get_client().table("users").upsert(
            {"dataset_source": dataset_source, "user_id": order.user_id},
            on_conflict="dataset_source,user_id",
        ).execute()
    except Exception as e:
        print(f"[orders] user upsert 실패: {e}")
        raise HTTPException(status_code=500, detail="고객 정보 처리에 실패했습니다")

    row = {
        "dataset_source": dataset_source,
        "order_id": order.order_id,
        "user_id": order.user_id,
        "order_date": order.order_date or datetime.now(timezone.utc).isoformat(),
        # orders.total_amount/discount_amount는 numeric(12,2)라 (달러 등 소수점 금액도 있음)
        # int로 반올림하지 않고 그대로 넘긴다.
        "total_amount": order.total_amount,
        "discount_amount": order.discount_amount,
        "coupon_used": order.coupon_used,
        "category": order.category,
    }
    try:
        get_client().table("orders").upsert(row, on_conflict="dataset_source,order_id").execute()
    except Exception as e:
        print(f"[orders] insert 실패: {e}")
        raise HTTPException(status_code=500, detail="주문 저장에 실패했습니다")
    return {"ok": True}
