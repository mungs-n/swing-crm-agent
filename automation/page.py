import streamlit as st
import pandas as pd
from automation.campaign_builder import render_campaign_builder
from automation.email_sender import render_history, get_scheduler, render_recurring_campaigns_panel

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

st.set_page_config(layout="wide")

col_title, col_btn = st.columns([4, 1])
with col_title:
    st.title("캠페인 관리")

with col_btn:
    if st.button("+ 캠페인 생성하기", type="primary", use_container_width=True):
        st.session_state["show_campaign_builder"] = True

# 검색 및 필터 바 (이미지 디자인 반영)
fcol1, fcol2, fcol3 = st.columns([2, 1, 1])
with fcol1:
    search_text = st.text_input(
        "검색", placeholder="캠페인명 또는 키워드 검색", label_visibility="collapsed"
    )
with fcol2:
    status_filter = st.selectbox(
        "상태 필터",
        ["전체", "초안", "임시 저장", "테스트 발송", "예약 대기", "반복 발송", "발송 완료"],
        label_visibility="collapsed",
    )
with fcol3:
    st.selectbox("태그 선택", ["태그 전체"], label_visibility="collapsed")

# 캠페인 생성 모달/화면 전환 처리
if st.session_state.get("show_campaign_builder", False):
    with st.expander("🚀 신규 캠페인 생성하기", expanded=True):
        render_campaign_builder()
        if st.button("닫기"):
            st.session_state["show_campaign_builder"] = False
            st.rerun()


# ==============================================================================
# 캠페인 목록 테이블 (1번 사진 디자인 반영)
#
# data/campaign_history.csv 에는 캠페인 "이름", "태그", "발송 유형" 같은 컬럼이
# 원래 없어서(세그먼트/메시지 요약/상태 문장만 저장됨), 있는 값들로부터 최대한
# 자연스럽게 유추해서 채운다. 나중에 csv 스키마 자체를 확장하면 이 부분을
# 실제 저장된 값으로 그대로 교체하면 된다.
# ==============================================================================

CHANNEL_LABELS = {
    "카카오톡": "카카오톡 🟡",
    "문자": "문자(SMS/LMS) 💬",
    "웹 푸시": "웹 푸시 🔔",
    "이메일": "이메일 ✉️",
}


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


def _channel_badge(raw_status: str) -> str:
    for key, label in CHANNEL_LABELS.items():
        if key in str(raw_status):
            return label
    return ""


@st.cache_data(ttl=5)
def _load_campaign_rows() -> pd.DataFrame:
    try:
        df = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig")
    except FileNotFoundError:
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.reset_index(drop=True)
    df["키"] = range(1, len(df) + 1)

    dot_and_label = df["상태"].map(_status_info)
    df["_dot"] = dot_and_label.map(lambda t: t[0])
    df["상태_표시"] = dot_and_label.map(lambda t: t[1])
    df["발송 유형"] = df["상태"].map(_send_type)
    df["채널"] = df["상태"].map(_channel_badge)
    df["이름"] = df["세그먼트"].astype(str) + " 캠페인"
    df["발송 대상"] = df["세그먼트"]
    df["생성일"] = df["발송일시"].astype(str).str.slice(0, 10)
    df["태그"] = "-"

    # 최신 캠페인이 위로 오도록
    return df.iloc[::-1].reset_index(drop=True)


_TABLE_COL_RATIO = [0.4, 2.2, 1.3, 2, 1.8, 0.8, 1.1]


def render_campaign_table(search_text: str = "", status_filter: str = "전체"):
    df = _load_campaign_rows()

    if df.empty:
        st.info("아직 생성된 캠페인이 없습니다. 우측 상단 [+ 캠페인 생성하기]로 첫 캠페인을 만들어보세요.")
        return

    filtered = df
    if search_text:
        mask = (
            filtered["이름"].astype(str).str.contains(search_text, case=False, na=False)
            | filtered["발송 대상"].astype(str).str.contains(search_text, case=False, na=False)
        )
        filtered = filtered[mask]

    if status_filter and status_filter != "전체":
        filtered = filtered[filtered["상태_표시"] == status_filter]

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
            color: #6B7280;
            background: #F3F4F6;
            padding: 2px 7px;
            border-radius: 6px;
            white-space: nowrap;
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

        /* '이름' 열: 보이는 텍스트(.ct-name-visible)는 다른 칸과 완전히 같은
           방식으로 그리고, 그 칸을 담고 있는 컬럼을 기준(position:relative)으로
           삼아 버튼을 투명하게(opacity:0) 그 위에 꽉 채워 덮어씌운다.
           버튼은 눈에 안 보이고 클릭만 받는 용도. */
        div[data-testid="stHorizontalBlock"] > div:has(button[data-testid="stBaseButton-tertiary"]),
        div[data-testid="stHorizontalBlock"] > div:has(button[kind="tertiary"]) {
            position: relative;
        }
        .ct-name-visible {
            color: #7C3AED;
            font-weight: 500;
            pointer-events: none; /* 텍스트 자체는 클릭을 막고, 아래 버튼이 받게 함 */
        }
        div[data-testid="stButton"] {
            position: absolute !important;
            inset: 0 !important;
            margin: 0 !important;
        }
        div[data-testid="stButton"] > button[kind="tertiary"],
        button[data-testid="stBaseButton-tertiary"] {
            position: absolute !important;
            inset: 0 !important;
            width: 100% !important;
            height: 100% !important;
            opacity: 0 !important;
            cursor: pointer;
            padding: 0 !important;
            margin: 0 !important;
        }
        /* 버튼(투명)에 마우스가 올라가면 그 위에 겹쳐진 텍스트에 밑줄 표시 */
        div[data-testid="stHorizontalBlock"] > div:has(button[data-testid="stBaseButton-tertiary"]:hover) .ct-name-visible,
        div[data-testid="stHorizontalBlock"] > div:has(button[kind="tertiary"]:hover) .ct-name-visible {
            text-decoration: underline;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if filtered.empty:
        st.markdown(
            "<div style='padding:32px; text-align:center; color:#9CA3AF; "
            "border:1px solid #E5E7EB; border-radius:12px;'>검색/필터 조건에 맞는 캠페인이 없습니다.</div>",
            unsafe_allow_html=True,
        )
        return

    with st.container(border=True):
        header_cols = st.columns(_TABLE_COL_RATIO)
        for col, label in zip(header_cols, ["키", "이름", "상태", "발송 대상", "발송 유형", "태그", "생성일"]):
            col.markdown(f"<span class='ct-th'>{label}</span>", unsafe_allow_html=True)

        st.markdown("<hr style='margin:6px 0 4px 0;border-color:#E5E7EB'>", unsafe_allow_html=True)

        for _, row in filtered.iterrows():
            row_cols = st.columns(_TABLE_COL_RATIO)

            with row_cols[0]:
                st.markdown(f"<div class='ct-row-cell ct-key'>{row['키']}</div>", unsafe_allow_html=True)
            with row_cols[1]:
                # 보이는 건 다른 칸과 똑같은 순수 텍스트(ct-row-cell)로 그리고,
                # 그 위에 안 보이는(opacity:0) 버튼을 정확히 겹쳐서 클릭만 받는다.
                # → Streamlit 버튼 자체의 내부 여백/줄간격이 버전마다 달라서
                #   텍스트로 보이는 버튼은 계속 위아래로 미세하게 어긋났었음.
                st.markdown(
                    f"<div class='ct-row-cell ct-name-visible'>{row['이름']}</div>",
                    unsafe_allow_html=True,
                )
                if st.button(row["이름"], key=f"camp_name_{row['키']}", type="tertiary"):
                    current = st.session_state.get("selected_campaign_key")
                    st.session_state["selected_campaign_key"] = None if current == row["키"] else row["키"]
                    st.rerun()
            with row_cols[2]:
                st.markdown(
                    f"<div class='ct-row-cell'><span class='ct-status-dot' style='background:{row['_dot']}'></span>{row['상태_표시']}</div>",
                    unsafe_allow_html=True,
                )
            with row_cols[3]:
                channel_html = f"<span class='ct-channel'>{row['채널']}</span>" if row["채널"] else ""
                st.markdown(f"<div class='ct-row-cell'>{row['발송 대상']}{channel_html}</div>", unsafe_allow_html=True)
            with row_cols[4]:
                st.markdown(f"<div class='ct-row-cell'>{row['발송 유형']}</div>", unsafe_allow_html=True)
            with row_cols[5]:
                st.markdown(f"<div class='ct-row-cell ct-muted'>{row['태그']}</div>", unsafe_allow_html=True)
            with row_cols[6]:
                st.markdown(f"<div class='ct-row-cell ct-muted'>{row['생성일']}</div>", unsafe_allow_html=True)

            if st.session_state.get("selected_campaign_key") == row["키"]:
                send_datetime = str(row.get("발송일시", "")).strip()
                date_part, _, time_part = send_datetime.partition(" ")
                # 메시지 요약은 save_history()에서 더 이상 잘라 저장하지 않으므로
                # 여기서도 그대로 전체를 보여준다 (별도로 자르는 로직 없음).
                content = row.get("메시지 요약", "") or "(내용 없음)"

                st.markdown(
                    "<div class='ct-detail-box'>"
                    f"<b>발송 날짜:</b> {date_part or '-'} &nbsp;&nbsp; "
                    f"<b>발송 시간:</b> {time_part or '-'}<br><br>"
                    f"<b>내용</b><br>{content}"
                    "</div>",
                    unsafe_allow_html=True,
                )

            st.markdown("<hr style='margin:8px 0;border-color:#F3F4F6'>", unsafe_allow_html=True)

    st.caption(f"총 {len(filtered)}개")


render_campaign_table(search_text=search_text, status_filter=status_filter)

# 반복 발송 캠페인 목록 (일시정지/삭제)
render_recurring_campaigns_panel()

# 예약/완료 발송 상세 이력은 접어서 그 아래에 유지
render_history()