"""
캠페인 퍼포먼스 대시보드 차트
담당: 가연
"""

import plotly.graph_objects as go
import streamlit as st

from performance.constants import CHANNEL_META, LINE, METRIC_DEFS


def render_trend_chart(trend_df):
    """'자동화 성과 추이' - 발송/클릭/전환/전환매출을 토글로 켜고 끄는 라인 차트.
    전환매출만 revenue 단위가 커서 우측(보조) y축을 쓴다."""
    st.markdown("##### 자동화 성과 추이")

    selected = st.multiselect(
        "표시할 지표", [m["key"] for m in METRIC_DEFS],
        default=["conversions", "revenue"],
        format_func=lambda k: next(m["label"] for m in METRIC_DEFS if m["key"] == k) +
        (" (우측축)" if next(m["axis"] for m in METRIC_DEFS if m["key"] == k) == "right" else ""),
        label_visibility="collapsed",
        key="perf-trend-metrics",
    )

    if trend_df.empty or not selected:
        st.info("선택한 기간에 발송 데이터가 없어요.")
        return

    fig = go.Figure()
    for m in METRIC_DEFS:
        if m["key"] not in selected:
            continue
        fig.add_trace(go.Scatter(
            x=trend_df["date"], y=trend_df[m["key"]], name=m["label"], mode="lines",
            line=dict(color=m["color"], width=2.5),
            yaxis="y2" if m["axis"] == "right" else "y",
        ))

    fig.update_layout(
        height=280,
        xaxis=dict(showgrid=False),
        yaxis=dict(title=None, gridcolor=LINE),
        yaxis2=dict(overlaying="y", side="right", showgrid=False, tickformat=",.0f"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(t=30, l=10, r=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch", key="perf-trend-chart")


def render_channel_chart(channel_df):
    """'채널별 성과' - 채널별 전환율 막대 그래프."""
    st.markdown("##### 채널별 성과")

    if channel_df.empty:
        st.info("선택한 기간에 발송 데이터가 없어요.")
        return

    ordered = channel_df.set_index("channel").reindex(CHANNEL_META.keys()).dropna(how="all").reset_index()
    labels = [CHANNEL_META[c]["label"] for c in ordered["channel"]]
    colors = [CHANNEL_META[c]["color"] for c in ordered["channel"]]

    fig = go.Figure(go.Bar(
        x=labels, y=ordered["cvr"], marker_color=colors,
        text=[f"{v:.1f}%" for v in ordered["cvr"]], textposition="outside",
    ))
    fig.update_layout(
        height=240,
        yaxis=dict(title="전환율(%)", gridcolor=LINE),
        xaxis=dict(showgrid=False),
        margin=dict(t=20, l=10, r=10, b=10),
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
    )
    st.plotly_chart(fig, width="stretch", key="perf-channel-chart")
