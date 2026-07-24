import streamlit as st

from components.charts import render_charts

st.set_page_config(
    page_title="대시보드 - ATHLEPA CRM",
    page_icon="assets/athlepa_logo.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.logo("assets/athlepa_logo.svg", size="large")

st.title("대시보드")
render_charts()