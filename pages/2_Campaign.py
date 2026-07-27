import streamlit as st

from components.campaign_builder import render_campaign_builder
from components.email_sender import render_history

st.title(":material/bolt: 캠페인 자동화")
render_campaign_builder()
st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
render_history()
