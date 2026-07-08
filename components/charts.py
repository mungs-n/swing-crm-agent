"""
담당자: 탭1 차트 담당
작업 내용: KPI 카드, GMV 차트, 세그먼트 도넛, RFM 산포도, 퍼널, 코호트 히트맵
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def load_data():
    """데이터 로드 함수"""
    users = pd.read_csv("data/users.csv")
    orders = pd.read_csv("data/orders.csv")
    events = pd.read_csv("data/events.csv")
    return users, orders, events


def render_kpi_cards(users, orders):
    """KPI 스코어 카드 5개"""
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("활성 고객 수", "742명", "+3.2%")
    with col2:
        st.metric("GMV", "₩99.7M", "+10.8%")
    with col3:
        st.metric("AOV", "₩37,900", "-0.1%")
    with col4:
        st.metric("구매 전환율", "6.2%", "+13.3%")
    with col5:
        st.metric("30일 이탈률", "18.4%", "+2.1%p")


def render_gmv_chart(orders):
    """GMV & 주문 수 추이 콤보 차트"""
    st.subheader("GMV & 주문 수 추이")
    # TODO: 실제 데이터로 교체
    fig = go.Figure()
    st.plotly_chart(fig, use_container_width=True)


def render_segment_donut(users):
    """고객 세그먼트 분포 도넛 차트"""
    st.subheader("고객 세그먼트 분포")
    # TODO: 실제 데이터로 교체
    fig = go.Figure()
    st.plotly_chart(fig, use_container_width=True)


def render_funnel(events):
    """구매 퍼널 차트"""
    st.subheader("구매 퍼널")
    # TODO: 실제 데이터로 교체
    fig = go.Figure()
    st.plotly_chart(fig, use_container_width=True)


def render_cohort(orders):
    """코호트 리텐션 히트맵"""
    st.subheader("코호트 리텐션 히트맵")
    # TODO: 실제 데이터로 교체
    fig = go.Figure()
    st.plotly_chart(fig, use_container_width=True)


def render_charts():
    """메인 렌더 함수 - Dashboard.py에서 호출"""
    try:
        users, orders, events = load_data()
    except FileNotFoundError:
        st.warning("데이터 파일을 찾을 수 없습니다. data/ 폴더에 CSV 파일을 넣어주세요.")
        return

    render_kpi_cards(users, orders)

    col1, col2 = st.columns(2)
    with col1:
        render_gmv_chart(orders)
    with col2:
        render_segment_donut(users)

    col3, col4 = st.columns(2)
    with col3:
        render_funnel(events)
    with col4:
        render_cohort(orders)
