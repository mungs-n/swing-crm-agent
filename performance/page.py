"""
캠페인 퍼포먼스 대시보드 서브탭 진입점
담당: 가연

automation/page.py에서 서브탭(캠페인 만들기 / A/B 테스트 / 퍼포먼스 대시보드)을 만들 때
'퍼포먼스 대시보드' 서브탭에서 render_performance()를 호출하면 됩니다.

예)
    tab_build, tab_ab, tab_perf = st.tabs(["캠페인 만들기", "A/B 테스트", "퍼포먼스 대시보드"])
    with tab_perf:
        from performance.page import render_performance
        render_performance()
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from performance.charts import render_channel_chart, render_trend_chart
from performance.constants import ACCENT, CHANNEL_META, IMPACT_BASIS_META, STATUS_META
from performance.data import (
    build_trend_series, campaign_table, channel_performance, kpi_summary, load_campaign_history, uplift_summary,
)
from ab_test.data import load_campaign_sends


def _pct_delta(current, previous):
    """전기 대비 증감률(%). 전기 값이 0이면 0 반환 (dashboard/charts.py와 동일 로직)"""
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


def _trend_pill(delta: float, unit: str = "%") -> str:
    if delta > 0:
        cls, arrow = "trend-up", "▲"
    elif delta < 0:
        cls, arrow = "trend-down", "▼"
    else:
        cls, arrow = "trend-neutral", ""
    return f"<span class='trend-pill {cls}'>{arrow} {abs(delta):.1f}{unit}</span>"


def _kpi_card(label: str, value: str, delta: float | None = None, unit: str = "%"):
    """dashboard/charts.py의 _kpi_card와 같은 스타일(테마 CSS의 stat-card/trend-pill 클래스 재사용).
    delta가 None이면 배지 없이 값만 표시(석세스 매트릭스 카드용)."""
    trend_html = _trend_pill(delta, unit) if delta is not None else ""
    st.markdown(
        f"<div class='stat-card-top'><span class='stat-label'>{label}</span>{trend_html}</div>"
        f"<div class='stat-value'>{value}</div>"
        + (f"<div class='stat-sub'>이전 기간 대비</div>" if delta is not None else ""),
        unsafe_allow_html=True,
    )


def _fmt_won(v: float) -> str:
    return f"₩{v / 10000:,.0f}만"


def _fmt_pct(v) -> str:
    return "-" if pd.isna(v) else f"{v:.1f}%"


def _fmt_pct_signed(v) -> str:
    return "-" if pd.isna(v) else f"{v:+.1f}%p"


def _render_kpis(kpi: dict, kpi_prev: dict):
    cols = st.columns(5)
    items = [
        ("발송 메시지 수", f"{kpi['sent']:,}", _pct_delta(kpi["sent"], kpi_prev["sent"]), "%"),
        ("클릭률", _fmt_pct(kpi["ctr"]), kpi["ctr"] - kpi_prev["ctr"], "%p"),
        ("전환율", _fmt_pct(kpi["cvr"]), kpi["cvr"] - kpi_prev["cvr"], "%p"),
        ("전환 구매금액", _fmt_won(kpi["revenue"]), _pct_delta(kpi["revenue"], kpi_prev["revenue"]), "%"),
        ("자동화 기여 매출 비중", f"{kpi['auto_share']:.0f}%", kpi["auto_share"] - kpi_prev["auto_share"], "%p"),
    ]
    for col, (label, value, delta, unit) in zip(cols, items):
        with col:
            with st.container(border=True):
                _kpi_card(label, value, delta, unit)


def _render_uplift_cards(uplift: dict):
    """'전환율 증분 (자동화 효과)' / '증분 매출 (자동화 효과)' - Success Matrix 카드."""
    cols = st.columns(2)
    with cols[0]:
        with st.container(border=True):
            _kpi_card("전환율 증분 (자동화 효과)", _fmt_pct_signed(uplift["cvr_uplift"]))
    with cols[1]:
        value = "-" if uplift["revenue_uplift"] is None else f"{'+' if uplift['revenue_uplift'] >= 0 else ''}{_fmt_won(uplift['revenue_uplift'])}"
        with st.container(border=True):
            _kpi_card("증분 매출 (자동화 효과)", value)
    st.caption(
        "* 자동화를 하지 않았을 때의 예상 수치 대비 늘어난 정도입니다. AB 테스트가 있는 캠페인은 "
        "실제 대조군과 비교한 값, 없는 캠페인은 같은 세그먼트의 다른 캠페인 평균과 비교한 값(참고용)을 "
        "함께 반영한 수치입니다."
    )


def _status_badge(status: str) -> str:
    meta = STATUS_META.get(status, {"color": "#6B7280", "label": status})
    return (
        f"<span style='font-size:11px;font-weight:600;color:{meta['color']};"
        f"background:{meta['color']}14;padding:2px 8px;border-radius:20px'>{meta['label']}</span>"
    )


def _channel_badge(channel: str) -> str:
    meta = CHANNEL_META.get(channel, {"color": "#6B7280", "label": channel})
    return (
        f"<span style='font-size:11px;font-weight:600;color:{meta['color']};"
        f"background:{meta['color']}14;padding:2px 8px;border-radius:20px'>{meta['label']}</span>"
    )


def _impact_badge(basis) -> str:
    if pd.isna(basis) or basis not in IMPACT_BASIS_META:
        return "<span style='color:#C7CAD6;font-size:11px'>-</span>"
    meta = IMPACT_BASIS_META[basis]
    return (
        f"<span style='font-size:11px;font-weight:600;color:{meta['color']};"
        f"background:{meta['color']}14;padding:2px 8px;border-radius:20px'>{meta['label']}</span>"
    )


def _render_campaign_table(table_df: pd.DataFrame):
    st.markdown("##### 캠페인별 상세 성과")
    if table_df.empty:
        st.info("선택한 기간에 집계할 캠페인이 없어요.")
        return

    headers = ["캠페인", "채널", "상태", "발송성공", "클릭률", "전환율(전체)", "전환율(직접)", "전환매출", "전환율 증분", "임팩트 근거"]
    header_html = "".join(f"<th style='text-align:left;padding:8px 10px;color:#6B7280;font-size:11.5px'>{h}</th>" for h in headers)

    rows_html = []
    for _, r in table_df.iterrows():
        has_uplift = not pd.isna(r["cvr_uplift"])
        uplift_color = ("#10B981" if r["cvr_uplift"] > 0 else "#F43F5E") if has_uplift else "#C7CAD6"
        has_direct = not pd.isna(r["cvr_direct"])
        rows_html.append(
            "<tr style='border-bottom:1px solid #E7E9F3'>"
            f"<td style='padding:10px;font-weight:600'>{r['name']}</td>"
            f"<td style='padding:10px'>{_channel_badge(r['channel'])}</td>"
            f"<td style='padding:10px'>{_status_badge(r['status'])}</td>"
            f"<td style='padding:10px'>{int(r['sent']):,}</td>"
            f"<td style='padding:10px'>{_fmt_pct(r['ctr'])}</td>"
            f"<td style='padding:10px'>{_fmt_pct(r['cvr_total'])}</td>"
            f"<td style='padding:10px;color:{'#1C2130' if has_direct else '#C7CAD6'}'>"
            f"{_fmt_pct(r['cvr_direct']) if has_direct else '추적 불가'}</td>"
            f"<td style='padding:10px;font-weight:700;color:{ACCENT}'>{_fmt_won(r['revenue'])}</td>"
            f"<td style='padding:10px;font-weight:600;color:{uplift_color}'>"
            f"{_fmt_pct_signed(r['cvr_uplift'])}</td>"
            f"<td style='padding:10px'>{_impact_badge(r['impact_basis'])}</td>"
            "</tr>"
        )

    table_html = (
        "<div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse;font-size:13px'>"
        f"<thead><tr style='border-bottom:1px solid #E7E9F3'>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption(
        "* 카카오톡·메시지 채널은 클릭 추적이 불가해 전환율(직접)을 집계하지 않습니다.\n\n"
        "* 전환율 증분은 자동화를 하지 않았을 때의 예상 수치 대비 늘어난 정도의 근사치입니다. "
        "\"AB 테스트 기반\"은 실제 대조군과 비교한 값이라 신뢰도가 높고, \"전후 비교(참고용)\"는 "
        "같은 세그먼트의 다른 캠페인 평균과 비교한 값이라 참고용으로만 봐야 합니다."
    )


def render_performance():
    """퍼포먼스 대시보드 전체 렌더 - automation/page.py의 '퍼포먼스 대시보드' 서브탭에서 호출."""
    st.markdown("##### 퍼포먼스 대시보드")

    col_spacer, col_range = st.columns([3, 2])
    with col_range:
        default_start = date(2026, 1, 1)
        default_end = date.today()
        picked = st.date_input(
            "기간", value=(default_start, default_end), label_visibility="collapsed", key="perf-date-range",
        )
    if len(picked) != 2:
        st.info("종료일까지 선택해주세요.")
        return
    start, end = picked
    if start > end:
        st.warning("시작일이 종료일보다 늦어요.")
        return

    sends_df = load_campaign_sends()
    history_df = load_campaign_history()
    campaign_df = campaign_table(sends_df, history_df, start, end)

    # KPI 비교 기준: 선택 기간 바로 직전의 '같은 길이' 기간 (dashboard/charts.py와 동일한 방식)
    span_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span_days - 1)

    kpi = kpi_summary(sends_df, start, end)
    kpi_prev = kpi_summary(sends_df, prev_start, prev_end)
    _render_kpis(kpi, kpi_prev)

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    _render_uplift_cards(uplift_summary(campaign_df))

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        render_trend_chart(build_trend_series(sends_df, start, end))

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        render_channel_chart(channel_performance(sends_df, start, end))

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        _render_campaign_table(campaign_df)
