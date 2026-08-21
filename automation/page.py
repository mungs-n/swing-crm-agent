import streamlit as st
import pandas as pd

import math

from automation.campaign_builder import render_campaign_builder
from automation.email_sender import get_scheduler, render_recurring_campaigns_panel

HISTORY_FILE = "data/campaign_history.csv"

# 이 페이지가 열릴 때마다 호출되지만, get_scheduler()가 st.cache_resource로 감싸져
# 있어서 실제 백그라운드 스케줄러는 앱 프로세스당 한 번만 뜬다.
get_scheduler()

st.markdown(
    """
    <style>
        .block-container {
            padding-top: 3rem !important; /* 상단 여백을 3rem만큼 확보 */
        }
    </style>
    """,
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
        /* 제목과 우측 버튼의 세로 중앙 정렬 (버튼이 st.title보다 낮게 보이던 문제 수정) */
        div[data-testid="stHorizontalBlock"]:has(> div [data-testid="stMarkdownContainer"] h1) {
            align-items: center;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==============================================================================
# 캠페인 목록 테이블
#
# data/campaign_history.csv 에는 캠페인 "이름", "발송 유형" 같은 컬럼이 원래
# 없어서(세그먼트/메시지 요약/상태 문장만 저장됨), 있는 값들로부터 최대한
# 자연스럽게 유추해서 채운다. 나중에 csv 스키마 자체를 확장하면 이 부분을
# 실제 저장된 값으로 그대로 교체하면 된다.
# ==============================================================================

STATUS_OPTIONS = ["초안", "임시 저장", "테스트 발송", "예약 대기", "반복 발송", "발송 완료"]

# (표시 라벨, 배경색, 글자색) - 참고 이미지 색상 팔레트에 맞춤
CHANNEL_STYLES = {
    "카카오톡": ("카카오톡", "#FFEDD5", "#C2703D"),
    "문자": ("문자(SMS/LMS)", "#DBEAFE", "#1E40AF"),
    "웹 푸시": ("웹 푸시", "#DCFCE7", "#2F9E44"),
    "이메일": ("이메일", "#F3E8FF", "#7C3AED"),
}

PAGE_SIZE = 10


def _status_info(raw_status: str):
    """상태 문장(예: '전체 발송 완료 (...)')에서 점 색상과 짧은 라벨을 뽑아낸다."""
    s = str(raw_status)
    if "반복" in s:
        return "#7C3AED", "반복 발송"
    if "예약" in s:
        return "#F59E0B", "예약 대기"
    if "완료" in s:
        return "#9CA3AF", "발송 완료"
    if "테스트" in s:
        return "#111827", "테스트 발송"
    if "초안" in s:
        return "#9CA3AF", "초안"
    return "#111827", "임시 저장"


def _send_type(raw_status: str) -> str:
    s = str(raw_status)
    if "예약" in s:
        return "스케줄 기반 - 예약"
    if "완료" in s or "테스트" in s:
        return "스케줄 기반 - 즉시"
    return "-"


def _channel_badge(raw_status: str):
    """상태 문장에서 채널을 찾아 (표시 라벨, 배경색, 글자색)을 반환한다."""
    s = str(raw_status)
    for key, style in CHANNEL_STYLES.items():
        if key in s:
            return style
    return None


@st.cache_data(ttl=5)
def _load_campaign_rows() -> pd.DataFrame:
    try:
        df = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.reset_index(drop=True)

    dot_and_label = df["상태"].map(_status_info)
    df["_dot"] = dot_and_label.map(lambda t: t[0])
    df["상태_표시"] = dot_and_label.map(lambda t: t[1])
    df["발송 유형"] = df["상태"].map(_send_type)

    channel_style = df["상태"].map(_channel_badge)
    df["채널"] = channel_style.map(lambda t: t[0] if t else "")
    df["_채널_bg"] = channel_style.map(lambda t: t[1] if t else "")
    df["_채널_fg"] = channel_style.map(lambda t: t[2] if t else "")

    df["이름"] = df["세그먼트"].astype(str) + " 캠페인"
    df["발송 대상"] = df["세그먼트"]
    df["생성일"] = df["발송일시"].astype(str).str.slice(0, 10)

    # 키 번호는 생성된 순서(오름차순, 1=가장 오래된 캠페인)를 유지하되,
    # 실제 화면에는 가장 최근에 생성된 캠페인이 맨 위로 오도록 뒤집어서 보여준다.
    df = df.reset_index(drop=True)
    df["키"] = range(1, len(df) + 1)
    df = df.iloc[::-1].reset_index(drop=True)
    return df


_TABLE_COL_RATIO = [0.4, 2.2, 1.3, 2.2, 1.8, 1.1]


def _status_filter_state():
    """세션에 저장된 상태 필터 체크 상태를 읽는다 (위젯을 그리지는 않음)."""
    for status in STATUS_OPTIONS:
        st.session_state.setdefault(f"status_chk_{status}", True)
    return {s for s in STATUS_OPTIONS if st.session_state.get(f"status_chk_{s}", True)}


def _status_header_popover():
    """테이블 헤더의 '상태' 칸 자체를 눌렀을 때 뜨는 팝오버.
    (GitHub의 '브랜치 전환' 버튼처럼, 목록 위에 따로 필터 바를 두지 않고
    헤더 라벨 자리에서 바로 상태를 체크박스로 선택한다.)"""
    chosen = [s for s in STATUS_OPTIONS if st.session_state.get(f"status_chk_{s}", True)]
    if len(chosen) == len(STATUS_OPTIONS):
        suffix = ""
    elif len(chosen) == 0:
        suffix = " (없음)"
    elif len(chosen) <= 2:
        suffix = f" ({', '.join(chosen)})"
    else:
        suffix = f" ({len(chosen)}개)"

    st.markdown("<span class='ct-status-th-anchor'></span>", unsafe_allow_html=True)
    with st.popover(f"상태{suffix}"):
        st.caption("표시할 상태를 선택하세요 (복수 선택 가능)")
        for status in STATUS_OPTIONS:
            st.checkbox(status, key=f"status_chk_{status}")


def render_campaign_table(search_text: str = ""):
    df = _load_campaign_rows()

    if df.empty:
        st.info("아직 생성된 캠페인이 없습니다. 우측 상단 [+ 캠페인 생성하기]로 첫 캠페인을 만들어보세요.")
        return

    active_statuses = _status_filter_state()

    filtered = df
    if search_text:
        mask = (
            filtered["이름"].astype(str).str.contains(search_text, case=False, na=False)
            | filtered["발송 대상"].astype(str).str.contains(search_text, case=False, na=False)
        )
        filtered = filtered[mask]

    filtered = filtered[filtered["상태_표시"].isin(active_statuses)] if active_statuses else filtered.iloc[0:0]

    st.markdown(
        """
        <style>
        div[data-testid="stHorizontalBlock"] { align-items: center; }

        .ct-th { color: #6B7280; font-weight: 600; font-size: 0.75rem; }

        .ct-row-cell {
            display: flex;
            align-items: center;
            min-height: 34px;
            font-size: 0.85rem;
            color: #111827;
            line-height: 1.4;
        }
        .ct-key { color: #9CA3AF; }
        .ct-muted { color: #9CA3AF; }
        .ct-status-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            margin-right: 6px;
            flex-shrink: 0;
        }
        .ct-channel {
            margin-left: 6px;
            font-size: 0.72rem;
            padding: 2px 8px;
            border-radius: 999px;
            white-space: nowrap;
            font-weight: 500;
        }
        .ct-detail-box {
            background-color: #F8F9FA;
            border: 1px solid #E9ECEF;
            border-radius: 8px;
            padding: 12px 14px;
            font-size: 0.85rem;
            line-height: 1.5;
            color: #495057;
            white-space: pre-wrap;
        }

        /* '이름' 열: 실제 버튼 그 자체를 링크처럼 보이도록 스타일링한다.
           (예전에는 투명한 버튼을 텍스트 위에 absolute로 겹치는 방식이라
           버튼 내부 여백이 버전마다 달라 클릭 위치가 눈에 보이는 텍스트와
           어긋나는 문제가 있었다. 지금은 보이는 요소 = 클릭되는 요소라
           위치가 항상 정확히 일치한다.) */
        div[data-testid="stHorizontalBlock"] > div:has(.ct-name-marker) {
            margin-top: -6px;
        }
        div[data-testid="stHorizontalBlock"] > div:has(.ct-name-marker) button[data-testid="stBaseButton-tertiary"] {
            color: #7C3AED !important;
            font-weight: 500 !important;
            font-size: 0.85rem !important;
            justify-content: flex-start !important;
            text-align: left !important;
            padding: 6px 0 !important;
            min-height: 34px !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            width: 100%;
        }
        /* 버튼 내부에 Streamlit이 자체적으로 넣는 래퍼 div가 텍스트를 가운데
           정렬시키는 경우가 있어서, 안쪽 요소까지 전부 왼쪽 정렬로 강제한다.
           (이게 헤더의 '이름' 라벨과 실제 캠페인 이름이 서로 다른 위치에서
           시작하는 것처럼 보이던 원인이었다.) */
        div[data-testid="stHorizontalBlock"] > div:has(.ct-name-marker) button[data-testid="stBaseButton-tertiary"] div {
            justify-content: flex-start !important;
            text-align: left !important;
            width: 100% !important;
        }
        div[data-testid="stHorizontalBlock"] > div:has(.ct-name-marker) button[data-testid="stBaseButton-tertiary"]:hover {
            text-decoration: underline;
        }

        /* '상태' 헤더 칸: 팝오버를 여는 버튼을 다른 헤더 라벨(.ct-th)과
           똑같은 모양(작은 회색 글씨, 배경/테두리 없음)으로 보이게 한다. */
        div[data-testid="stHorizontalBlock"] > div:has(.ct-status-th-anchor) button {
            color: #6B7280 !important;
            font-weight: 600 !important;
            font-size: 0.75rem !important;
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin: 0 !important;
            min-height: auto !important;
            justify-content: flex-start !important;
        }
        div[data-testid="stHorizontalBlock"] > div:has(.ct-status-th-anchor) button:hover {
            color: #374151 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if filtered.empty:
        st.markdown(
            "<div style='padding:24px; text-align:left; color:#9CA3AF; "
            "border:1px solid #E5E7EB; border-radius:12px;'>검색/필터 조건에 맞는 캠페인이 없습니다.</div>",
            unsafe_allow_html=True,
        )
        return

    # --- 페이지네이션 (한 페이지당 10개). 마법사(캠페인 생성)의 "다음 단계"와는
    # 무관한, 목록만 넘기는 일반적인 페이지 이동이다. ---
    total_count = len(filtered)
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    if "campaign_table_page" not in st.session_state:
        st.session_state["campaign_table_page"] = 1
    current_page = min(max(1, st.session_state["campaign_table_page"]), total_pages)
    st.session_state["campaign_table_page"] = current_page

    start = (current_page - 1) * PAGE_SIZE
    page_df = filtered.iloc[start:start + PAGE_SIZE]

    with st.container(border=True):
        header_cols = st.columns(_TABLE_COL_RATIO)
        for col, label in zip(header_cols, ["키", "이름", "상태", "발송 대상", "발송 유형", "생성일"]):
            if label == "상태":
                with col:
                    _status_header_popover()
            else:
                col.markdown(f"<span class='ct-th'>{label}</span>", unsafe_allow_html=True)

        st.markdown("<hr style='margin:6px 0 4px 0;border-color:#E5E7EB'>", unsafe_allow_html=True)

        for _, row in page_df.iterrows():
            row_cols = st.columns(_TABLE_COL_RATIO)

            with row_cols[0]:
                st.markdown(f"<div class='ct-row-cell ct-key'>{row['키']}</div>", unsafe_allow_html=True)
            with row_cols[1]:
                st.markdown("<span class='ct-name-marker'></span>", unsafe_allow_html=True)
                if st.button(row["이름"], key=f"camp_name_{row['키']}", type="tertiary", use_container_width=True):
                    current = st.session_state.get("selected_campaign_key")
                    st.session_state["selected_campaign_key"] = None if current == row["키"] else row["키"]
                    st.rerun()
            with row_cols[2]:
                st.markdown(
                    f"<div class='ct-row-cell'><span class='ct-status-dot' style='background:{row['_dot']}'></span>{row['상태_표시']}</div>",
                    unsafe_allow_html=True,
                )
            with row_cols[3]:
                if row["채널"]:
                    channel_html = (
                        f"<span class='ct-channel' style='background:{row['_채널_bg']};"
                        f"color:{row['_채널_fg']}'>{row['채널']}</span>"
                    )
                else:
                    channel_html = ""
                st.markdown(f"<div class='ct-row-cell'>{row['발송 대상']}{channel_html}</div>", unsafe_allow_html=True)
            with row_cols[4]:
                st.markdown(f"<div class='ct-row-cell'>{row['발송 유형']}</div>", unsafe_allow_html=True)
            with row_cols[5]:
                st.markdown(f"<div class='ct-row-cell ct-muted'>{row['생성일']}</div>", unsafe_allow_html=True)

            if st.session_state.get("selected_campaign_key") == row["키"]:
                send_datetime = str(row.get("발송일시", "")).strip()
                date_part, _, time_part = send_datetime.partition(" ")
                # 메시지 요약은 save_history()에서 더 이상 잘라 저장하지 않으므로
                # 여기서도 그대로 전체를 보여준다 (별도로 자르는 로직 없음).
                content = row.get("메시지 요약", "") or "(내용 없음)"
                channel_name = row["채널"] or "-"

                st.markdown(
                    "<div class='ct-detail-box'>"
                    f"<b>발송 채널:</b> {channel_name} &nbsp;&nbsp; "
                    f"<b>발송 날짜:</b> {date_part or '-'} &nbsp;&nbsp; "
                    f"<b>발송 시간:</b> {time_part or '-'}<br><br>"
                    f"<b>내용</b><br>{content}"
                    "</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<hr style='margin:8px 0;border-color:#F3F4F6'>", unsafe_allow_html=True)

    pcol1, pcol2, pcol3, pcol_spacer = st.columns([0.6, 0.6, 2, 4])
    with pcol1:
        if st.button("이전", disabled=current_page <= 1, use_container_width=True, key="camp_page_prev"):
            st.session_state["campaign_table_page"] = current_page - 1
            st.rerun()
    with pcol2:
        if st.button("다음", disabled=current_page >= total_pages, use_container_width=True, key="camp_page_next"):
            st.session_state["campaign_table_page"] = current_page + 1
            st.rerun()
    with pcol3:
        st.markdown(
            f"<div style='text-align:left; color:#6B7280; font-size:0.85rem; padding-top:6px;'>"
            f"{current_page} / {total_pages} 페이지 · 총 {total_count}개</div>",
            unsafe_allow_html=True,
        )


col_title, col_btn = st.columns([4, 1])
with col_title:
    st.subheader("캠페인 관리")

with col_btn:
    if st.button("+ 캠페인 생성하기", type="primary", use_container_width=True):
        st.session_state["show_campaign_builder"] = True

# 검색 바
search_text = st.text_input(
    "검색", placeholder="캠페인명 또는 키워드 검색", label_visibility="collapsed"
)

# 캠페인 생성 모달/화면 전환 처리
if st.session_state.get("show_campaign_builder", False):
    with st.expander("신규 캠페인 생성하기", expanded=True):
        render_campaign_builder()
        if st.button("닫기"):
            st.session_state["show_campaign_builder"] = False
            st.rerun()

render_campaign_table(search_text=search_text)

# 반복 발송 캠페인 목록 (일시정지/삭제)
render_recurring_campaigns_panel()