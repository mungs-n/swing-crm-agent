"""
캠페인 퍼포먼스 대시보드 데이터 로딩 및 집계
담당: 가연

campaign_sends는 ab_test/data.py의 load_campaign_sends()를 그대로 재사용한다 (같은
Supabase 테이블, 같은 담당자 폴더라 중복 구현하지 않음). campaign_history는 여기서
별도로 Supabase에서 읽는다.

전환율 증분(cvr_uplift)/임팩트 근거(impact_basis)에 대한 중요한 전제: 실제 스키마에는
"이 캠페인이 어느 AB 테스트에서 나왔는지"를 잇는 FK가 없다. 그래서 세그먼트+채널이 같고
완료된 AB 테스트가 있으면 그 결과(winner 그룹의 uplift)를 가져다 쓰고("AB 테스트 기반"),
없으면 같은 세그먼트의 다른 캠페인들 평균 전환율 대비 이 캠페인의 개선율을 "참고용"으로
보여준다("전후 비교(참고용)"). 진짜 인과관계 검증이 아니라 근사치이므로 화면에도 그렇게
표시한다.
"""

import os
import re

import pandas as pd
import streamlit as st
from supabase import create_client

from ab_test.constants import CHANNEL_META as AB_CHANNEL_META
from ab_test.data import cvr, load_ab_tests, load_campaign_sends, two_proportion_test
from utils.data_loader import load_users_orders

# 합성 데이터 캠페인엔 진짜 이름이 없어서(메시지 요약이 '[합성 데이터] 세그먼트 대상
# channel 캠페인' 식의 기계적인 문자열뿐), 화면에서 알아보기 쉽도록 채널별로 그럴듯한
# 캠페인명 후보를 두고 campaign_id로 고정 배정한다. 실제 마케팅팀이 지은 이름이 아니라
# 표시용으로 붙이는 이름이라는 점에 주의 — 실제 콘텐츠(메시지 제목 등)와는 무관하다.
_CREATIVE_NAME_POOL = {
    "email": [
        "첫 구매 웰컴 시리즈", "이달의 신상 안내", "휴면 고객 재활성화 뉴스레터",
        "VIP 감사 캠페인", "생일 축하 쿠폰", "베스트셀러 위클리 픽",
    ],
    "kakao": [
        "오늘의 특가 알림", "친구 초대 이벤트", "재입고 알림톡",
        "포인트 소멸 안내", "카카오 단독 쿠폰",
    ],
    "sms": [
        "장바구니 리마인드 문자", "결제 임박 알림", "쿠폰 만료 안내", "재구매 유도 메시지",
    ],
    "webpush": [
        "장바구니 이탈 리마인드", "찜한 상품 할인 알림", "실시간 특가 푸시", "재입고 푸시 알림",
    ],
    "webpopup": [
        "시즌오프 웹 팝업", "첫 방문 웰컴 팝업", "장바구니 이탈 방지 팝업", "한정 수량 알림 팝업",
    ],
}


def _creative_campaign_names(campaign_ids_by_channel: dict) -> dict:
    """채널별 캠페인명 후보를 순번대로 하나씩 배정한다(campaign_id 정렬 순서대로 라운드로빈).
    해시로 배정하면 표본이 적을 때 우연히 같은 이름에 몰릴 수 있어서, 라운드로빈으로 확실히
    고르게 퍼지도록 한다. 정렬 순서 기준이라 같은 campaign_id 집합이면 항상 같은 이름이
    나온다. 실제 마케팅 카피가 아니라 화면 표시용으로 붙이는 이름."""
    result = {}
    for channel, campaign_ids in campaign_ids_by_channel.items():
        pool = _CREATIVE_NAME_POOL.get(channel)
        for i, campaign_id in enumerate(sorted(campaign_ids)):
            result[campaign_id] = pool[i % len(pool)] if pool else f"{channel} 캠페인"
    return result


CAMPAIGN_HISTORY_COLUMNS = [
    "campaign_id", "sent_at", "segment", "target_count", "message_summary", "status", "approval_mode",
]

# 채널별 클릭 추적 가능 여부 (ab_test/constants.py의 CHANNEL_META와 동일한 기준)
_CLICK_TRACKABLE = {ch: meta["click_trackable"] for ch, meta in AB_CHANNEL_META.items()}


def _get_client():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])


@st.cache_data(ttl=30, show_spinner="캠페인 이력을 불러오는 중...")
def load_campaign_history() -> pd.DataFrame:
    client = _get_client()
    rows = client.table("campaign_history").select("*").execute().data
    if not rows:
        return pd.DataFrame(columns=CAMPAIGN_HISTORY_COLUMNS)
    df = pd.DataFrame(rows)
    # format="ISO8601": 합성 데이터(초 단위)와 실시간 발송(마이크로초 단위)의 정밀도가
    # 달라서, format 지정 없이는 pandas가 일부 행에서 파싱 실패해 NaT가 되고, 그러면
    # 날짜 필터에서 그 캠페인이 통째로 빠진다 (ab_test/data.py와 동일한 이슈).
    df["sent_at"] = pd.to_datetime(df["sent_at"], errors="coerce", format="ISO8601")
    return df


def extract_campaign_name(message_summary: str, fallback: str) -> str:
    """캠페인 이름 전용 필드가 없어서 message_summary에서 뽑아 쓴다.
    - 실제 발송(email_sender.py가 저장): '제목: ...\\n\\n본문: ...' → 제목 줄을 그대로 쓴다
      (실제 데이터라 우선순위가 가장 높다).
    - 그 외(합성 데이터 등 제목이 없는 경우): fallback(채널별로 미리 배정해둔 그럴듯한
      캠페인명, _creative_campaign_names 참고)을 쓴다."""
    if isinstance(message_summary, str) and message_summary.strip():
        m = re.search(r"제목:\s*(.+)", message_summary)
        if m:
            title = m.group(1).strip()
            if title:
                return title[:30]

    return fallback


def _filter_range(df: pd.DataFrame, date_col: str, date_from, date_to) -> pd.DataFrame:
    mask = (df[date_col] >= pd.Timestamp(date_from)) & (df[date_col] < pd.Timestamp(date_to) + pd.Timedelta(days=1))
    return df[mask]


def kpi_summary(sends_df: pd.DataFrame, date_from, date_to) -> dict:
    """상단 KPI 카드용: 발송 메시지 수 / 클릭률 / 전환율 / 전환 구매금액 / 자동화 기여 매출 비중."""
    period = _filter_range(sends_df, "sent_at", date_from, date_to)
    delivered = period[period["delivered"]]

    sent = len(delivered)
    # _CLICK_TRACKABLE에 없는 채널 값(캠페인 만들기 쪽이 "카카오톡"처럼 내부 키가
    # 아닌 표시 라벨을 그대로 channel로 저장하는 경우 등)이 있으면 .map()이 NaN을
    # 돌려주는데, pandas는 NaN 섞인 값을 boolean 마스크로 못 써서 그대로 두면
    # ValueError가 난다. fillna(False)로 "추적 안 되는 채널"로 안전하게 처리한다.
    trackable = delivered[delivered["channel"].map(_CLICK_TRACKABLE).fillna(False)]
    clicks = trackable["clicked_at"].notna().sum()
    ctr = (clicks / len(trackable) * 100) if len(trackable) else 0.0

    conversions = delivered["converted_order_id"].notna().sum()
    cvr_total = (conversions / sent * 100) if sent else 0.0
    revenue = delivered["revenue"].fillna(0).sum()

    _, orders = load_users_orders("athlepa")
    if not orders.empty:
        orders_period = _filter_range(orders, "order_date", date_from, date_to)
        total_revenue = orders_period["total_amount"].sum()
    else:
        total_revenue = 0
    auto_share = (revenue / total_revenue * 100) if total_revenue else 0.0

    return {
        "sent": sent, "ctr": ctr, "cvr": cvr_total, "revenue": revenue, "auto_share": auto_share,
    }


def build_trend_series(sends_df: pd.DataFrame, date_from, date_to) -> pd.DataFrame:
    """'자동화 성과 추이' 차트용: 일자별 발송/클릭/전환/전환매출."""
    period = _filter_range(sends_df, "sent_at", date_from, date_to)
    delivered = period[period["delivered"]].copy()
    if delivered.empty:
        return pd.DataFrame(columns=["date", "sent", "clicks", "conversions", "revenue"])

    delivered["date"] = delivered["sent_at"].dt.date
    grouped = delivered.groupby("date").agg(
        sent=("send_id", "count"),
        clicks=("clicked_at", lambda s: s.notna().sum()),
        conversions=("converted_order_id", lambda s: s.notna().sum()),
        revenue=("revenue", lambda s: s.fillna(0).sum()),
    ).reset_index()
    return grouped


def channel_performance(sends_df: pd.DataFrame, date_from, date_to) -> pd.DataFrame:
    """'채널별 성과' 차트용: 채널별 발송 수 / 전환율 / 매출."""
    period = _filter_range(sends_df, "sent_at", date_from, date_to)
    delivered = period[period["delivered"]]
    if delivered.empty:
        return pd.DataFrame(columns=["channel", "sent", "cvr", "revenue"])

    grouped = delivered.groupby("channel").agg(
        sent=("send_id", "count"),
        conversions=("converted_order_id", lambda s: s.notna().sum()),
        revenue=("revenue", lambda s: s.fillna(0).sum()),
    ).reset_index()
    grouped["cvr"] = grouped.apply(lambda r: cvr(r["sent"], r["conversions"]), axis=1)
    return grouped


def _matching_ab_uplift(segment: str, channel: str, ab_df: pd.DataFrame):
    """세그먼트+채널이 같은 완료된 AB 테스트를 찾아서 winner 그룹의 컨트롤 대비 uplift를 돌려준다.
    못 찾으면 None."""
    if ab_df.empty:
        return None
    candidates = ab_df[
        (ab_df["segment"] == segment) & (ab_df["channel"] == channel) & (ab_df["status"] == "완료")
    ]
    if candidates.empty:
        return None

    test_id = candidates["test_id"].iloc[0]
    test_df = ab_df[ab_df["test_id"] == test_id]
    winner_id = test_df["winner_group_id"].iloc[0]
    control = test_df[test_df["is_control"]]
    winner = test_df[test_df["group_id"] == winner_id]
    if control.empty or winner.empty:
        return None

    c, w = control.iloc[0], winner.iloc[0]
    uplift, _, _ = two_proportion_test(int(c["users"]), int(c["conversions"]), int(w["users"]), int(w["conversions"]))
    return uplift


def campaign_table(sends_df: pd.DataFrame, history_df: pd.DataFrame, date_from, date_to) -> pd.DataFrame:
    """'캠페인별 상세 성과' 테이블용 행 목록."""
    period = _filter_range(sends_df, "sent_at", date_from, date_to)
    delivered = period[period["delivered"]]
    if delivered.empty or history_df.empty:
        return pd.DataFrame()

    ab_df = load_ab_tests()

    channel_by_campaign = delivered.groupby("campaign_id")["channel"].agg(lambda s: s.mode().iloc[0])
    campaign_ids_by_channel: dict = {}
    for cid, ch in channel_by_campaign.items():
        campaign_ids_by_channel.setdefault(ch, []).append(cid)
    creative_names = _creative_campaign_names(campaign_ids_by_channel)

    rows = []
    for campaign_id, g in delivered.groupby("campaign_id"):
        hist_row = history_df[history_df["campaign_id"] == campaign_id]
        if hist_row.empty:
            continue
        hist_row = hist_row.iloc[0]

        channel = g["channel"].mode().iloc[0]
        trackable = _CLICK_TRACKABLE.get(channel, False)
        sent = len(g)
        clicks = g["clicked_at"].notna().sum()
        ctr = (clicks / sent * 100) if sent and trackable else None

        conversions = g["converted_order_id"].notna().sum()
        cvr_total = cvr(sent, conversions)
        direct_conversions = (g["conversion_type"] == "direct").sum()
        cvr_direct = cvr(sent, direct_conversions) if trackable else None
        revenue = g["revenue"].fillna(0).sum()

        segment = hist_row["segment"]
        status = "scheduled" if hist_row["sent_at"] > pd.Timestamp.now() else "completed"

        uplift = _matching_ab_uplift(segment, channel, ab_df)
        if uplift is not None:
            impact_basis = "ab"
        else:
            others = delivered[(delivered["segment"] == segment) & (delivered["campaign_id"] != campaign_id)]
            baseline_cvr = cvr(len(others), others["converted_order_id"].notna().sum()) if len(others) else None
            if baseline_cvr:
                uplift = (cvr_total - baseline_cvr) / baseline_cvr * 100
                impact_basis = "prepost"
            else:
                uplift, impact_basis = None, None

        rows.append({
            "campaign_id": campaign_id,
            "name": extract_campaign_name(hist_row["message_summary"], creative_names.get(campaign_id, f"{channel} 캠페인")),
            "channel": channel,
            "status": status,
            "sent": sent,
            "ctr": ctr,
            "cvr_total": cvr_total,
            "cvr_direct": cvr_direct,
            "revenue": revenue,
            "cvr_uplift": uplift,
            "impact_basis": impact_basis,
        })

    return pd.DataFrame(rows).sort_values("revenue", ascending=False)


def uplift_summary(campaign_df: pd.DataFrame) -> dict:
    """상단 '전환율 증분 / 증분 매출' 카드용: campaign_table() 결과를 발송량 가중 평균해서
    하나의 숫자로 합친다. 매출 증분은 캠페인별 uplift(%)로 '자동화가 없었으면 이 매출이
    (1 + uplift/100)로 나눈 값이었을 것'이라고 역산해서, 그 차이를 다 더한 근사치다."""
    if campaign_df.empty or "cvr_uplift" not in campaign_df:
        return {"cvr_uplift": None, "revenue_uplift": None}

    valid = campaign_df[campaign_df["cvr_uplift"].notna() & (campaign_df["cvr_uplift"] > -100)]
    if valid.empty:
        return {"cvr_uplift": None, "revenue_uplift": None}

    weighted_cvr_uplift = (valid["cvr_uplift"] * valid["sent"]).sum() / valid["sent"].sum()
    baseline_revenue = valid["revenue"] / (1 + valid["cvr_uplift"] / 100)
    revenue_uplift = (valid["revenue"] - baseline_revenue).sum()

    return {"cvr_uplift": weighted_cvr_uplift, "revenue_uplift": revenue_uplift}
