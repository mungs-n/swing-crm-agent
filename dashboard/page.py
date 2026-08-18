import streamlit as st

from dashboard.charts import render_charts

st.title("대시보드")
render_charts()