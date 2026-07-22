import streamlit as st

from components.charts import render_charts

st.set_page_config(
    page_title="대시보드 - AI CRM",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("대시보드")
render_charts()