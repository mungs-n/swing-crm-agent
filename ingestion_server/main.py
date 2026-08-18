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


# 회사 수가 적고 api_key가 자주 바뀌지 않으므로 메모리에 캐시해서 매 요청마다
# companies 테이블을 조회하지 않게 한다.
_company_cache: dict[str, str] = {}


def resolve_dataset_source(api_key: str) -> str | None:
    if api_key in _company_cache:
        return _company_cache[api_key]
    res = get_client().table("companies").select("dataset_source").eq("api_key", api_key).limit(1).execute()
    if not res.data:
        return None
    dataset_source = res.data[0]["dataset_source"]
    _company_cache[api_key] = dataset_source
    return dataset_source


class TrackEvent(BaseModel):
    api_key: str
    event_type: str
    user_id: str
    session_id: str
    product_id: str | None = None
    category: str | None = None
    price: float | None = None


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
        "price": event.price,
    }
    get_client().table("events").insert(row).execute()
    return {"ok": True}
