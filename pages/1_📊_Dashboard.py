import streamlit as st
from components.charts import render_charts
from components.ai_insights import render_ai_panel

st.set_page_config(page_title="대시보드", page_icon="📊", layout="wide")
st.title("📊 CRM 대시보드")
st.markdown("---")

# 차트 섹션 (차트 담당자)
render_charts()

st.markdown("---")

# AI 인사이트 섹션 (AI 담당자)
render_ai_panel()
