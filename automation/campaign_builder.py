"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성
"""

import streamlit as st
import anthropic
from dotenv import load_dotenv
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import os
import random

from utils.rfm import calculate_rfm, assign_segment
from automation.email_sender import send_email, save_history, save_scheduled_emails, load_test_recipients, KST

load_dotenv()

STEP_LABELS = ["카피 작성", "발송 설정", "최종 확인"]


# 타겟 옵션은 세 가지 서로 다른 방식으로 오디언스를 계산한다:
# - "persona": data/generate_data.py가 가입 시점에 고정으로 부여한 성향 라벨(persona_type)
# - "rfm": 최근 구매 행동(Recency/Frequency/Monetary) 기준으로 매번 다시 계산되는 등급.
#   utils/rfm.py의 assign_segment()가 항상 4등급을 25%씩 균등하게 나눈다.
# - "cart_abandon": 장바구니에 담았지만 구매 이벤트가 없는 고객 (행동 기반, 실시간 계산)
#
# "이탈 위험 고객"(persona)과 "RFM: 이탈 위험"은 이름은 비슷하지만 계산 기준이 달라서
# 실제로는 서로 다른 사람 명단일 수 있다 — 라벨에 "RFM:" 접두어를 붙여 구분한다.
TARGET_OPTIONS = {
    "신규 탐색자": {
        "kind": "persona", "key": "new_explorer", "count": 150,
        "desc": ["가입 14일 이내", "첫 구매 유도 필요", "신규회원 전용 웰컴 혜택", "베스트셀러 추천"],
    },
    "충동 구매자": {
        "kind": "persona", "key": "impulsive_buyer", "count": 200,
        "desc": ["고빈도 중액", "신상품·기획전 반응 높음", "시각적 매력", "매진 임박", "최신 트렌드", "직관성", "긴박감"],
    },
    "할인 구매자": {
        "kind": "persona", "key": "discount_hunter", "count": 200,
        "desc": ["세일 시즌에만 반응", "할인 쿠폰이나 적립금 효과적", "할인율이나 할인 금액 명확히 보여주고 강조", "쿠폰 만료 임박 강조", "가성비", "긴박감"],
    },
    "브랜드 충성 고객": {
        "kind": "persona", "key": "brand_loyalist", "count": 150,
        "desc": ["중빈도 재구매", "VIP 혜택 선호", "브랜드에 대한 신뢰도 높음", "신제품 사전 공개"],
    },
    "이탈 위험 고객": {
        "kind": "persona", "key": "churn_risk", "count": 150,
        "desc": ["45일 이상 미구매", "재활성화 필요", "호기심 자극하는 메일 제목", "장바구니 상품 리마인드", "파격적인 리텐션 쿠폰이나 혜택", "놓치기 아쉬운 혜택 강조"],
    },
    "휴면 고객": {
        "kind": "persona", "key": "dormant", "count": 150,
        "desc": ["90일 이상 미방문", "윈백 캠페인 대상", "다시 돌아오면 제공되는 혜택", "강렬한 한 줄 소구로 극적인 이메일 제목"],
    },
    "RFM: VIP": {
        "kind": "rfm", "key": "VIP", "count": 0,
        "desc": ["최근성·구매빈도·구매액 종합 최상위 25%", "이미 충분히 좋은 고객", "감사 인사와 특별 대우", "VIP 전용 이벤트·독점 신제품 우선 공개"],
    },
    "RFM: 충성 고객": {
        "kind": "rfm", "key": "충성 고객", "count": 0,
        "desc": ["RFM 상위 2등급, 꾸준한 구매자", "리워드·적립 강조", "신제품 소식 우선 전달"],
    },
    "RFM: 이탈 위험": {
        "kind": "rfm", "key": "이탈 위험", "count": 0,
        "desc": ["RFM 하위 2등급, 최근 구매가 뜸해짐", "재구매 유도 쿠폰", "놓친 신상품 리마인드"],
    },
    "RFM: 휴면": {
        "kind": "rfm", "key": "휴면", "count": 0,
        "desc": ["RFM 종합 점수 최하위 25%", "장기 미구매·저빈도·저액", "윈백 캠페인", "파격 할인으로 재유입 유도"],
    },
    "장바구니 이탈 고객": {
        "kind": "cart_abandon", "key": None, "count": 0,
        "desc": ["상품을 장바구니에 담았지만 구매하지 않음", "구매 완결 유도", "장바구니 리마인드", "한정 할인·무료배송으로 전환 유도"],
    },
}


def classify_persona(df_users: pd.DataFrame, df_orders: pd.DataFrame = None) -> pd.DataFrame:
    """users.csv 및 orders.csv의 변수(가입일, 주문일, 할인/쿠폰 이력 등)를 이용한 자동 분류"""
    df = df_users.copy()
    
    if df_orders is not None and not df_orders.empty:
        latest_orders = df_orders.groupby('user_id').agg(
            last_order_date=('order_date', 'max'),
            coupon_count=('coupon_used', lambda x: x.sum() if x.dtype == 'bool' or x.dtype == 'int64' else 0),
            avg_discount=('discount_amount', 'mean')
        ).reset_index()
        df = pd.merge(df, latest_orders, on='user_id', how='left')
    
    today = pd.to_datetime('today')
    
    if 'signup_date' in df.columns:
        df['signup_date'] = pd.to_datetime(df['signup_date'])
        df['days_since_signup'] = (today - df['signup_date']).dt.days
    else:
        df['days_since_signup'] = 999

    if 'last_order_date' in df.columns:
        df['last_order_date'] = pd.to_datetime(df['last_order_date'])
        df['days_since_last_order'] = (today - df['last_order_date']).dt.days
    else:
        df['days_since_last_order'] = 999

    def assign_persona(row):
        if row['days_since_last_order'] >= 90:
            return "dormant"
        if row['days_since_last_order'] >= 45:
            return "churn_risk"
        if row['days_since_signup'] <= 14:
            return "new_explorer"
        if row.get('coupon_count', 0) > 0 or row.get('avg_discount', 0) > 0:
            return "discount_hunter"
        if row.get('acquisition_channel') in ['SNS', 'search_ad']:
            return "impulsive_buyer"
        return "brand_loyalist"

    df['persona_type'] = df.apply(assign_persona, axis=1)
    return df


def load_all_datasets():
    """CSV 데이터 파일 일괄 로드"""
    paths = {
        "users": ["users.csv", "data/users.csv"],
        "orders": ["orders.csv", "data/orders.csv"],
        "events": ["events.csv", "data/events.csv"]
    }
    dfs = {}
    for key, path_list in paths.items():
        for p in path_list:
            if os.path.exists(p):
                try:
                    dfs[key] = pd.read_csv(p)
                    break
                except Exception:
                    pass
        if key not in dfs:
            dfs[key] = pd.DataFrame()
    return dfs.get("users"), dfs.get("orders"), dfs.get("events")


def _rfm_audience_ids(df_orders: pd.DataFrame, tier: str) -> pd.Series:
    """RFM 등급(VIP/충성 고객/이탈 위험/휴면)에 해당하는 user_id만 골라 반환.
    utils.rfm.assign_segment()가 항상 4등급을 25%씩 균등하게 나눈다."""
    if df_orders.empty:
        return pd.Series(dtype=object)
    rfm = assign_segment(calculate_rfm(df_orders.copy()))
    return rfm.loc[rfm["segment"] == tier, "user_id"]


def _cart_abandon_ids(df_events: pd.DataFrame) -> set:
    """장바구니에 담았지만(add_to_cart) 구매(purchase) 이벤트가 없는 user_id 집합"""
    if df_events.empty or "event_type" not in df_events.columns:
        return set()
    cart_users = set(df_events.loc[df_events["event_type"] == "add_to_cart", "user_id"])
    purchase_users = set(df_events.loc[df_events["event_type"] == "purchase", "user_id"])
    return cart_users - purchase_users


def get_segment_info(segment_kr: str):
    """선택한 타겟 옵션의 실제 오디언스 수와 통계(평균 연령, 선호 카테고리)를 계산한다.
    옵션의 kind(persona/rfm/cart_abandon)에 따라 서로 다른 방식으로 대상을 추린다."""
    option = TARGET_OPTIONS.get(segment_kr, {})
    kind = option.get("kind", "persona")
    df_users, df_orders, df_events = load_all_datasets()

    if df_users.empty:
        return option.get("count", 0), {}

    if kind == "persona":
        if "persona_type" not in df_users.columns:
            df_users = classify_persona(df_users, df_orders)
        target_users = df_users[df_users["persona_type"] == option.get("key")]

    elif kind == "rfm":
        target_ids = _rfm_audience_ids(df_orders, option.get("key"))
        target_users = df_users[df_users["user_id"].isin(target_ids)]

    elif kind == "cart_abandon":
        target_ids = _cart_abandon_ids(df_events)
        target_users = df_users[df_users["user_id"].isin(target_ids)]

    else:
        target_users = df_users.iloc[0:0]

    user_count = len(target_users)

    if user_count == 0:
        return option.get("count", 0), {}

    stats = {}
    if "age" in target_users.columns:
        stats["avg_age"] = int(target_users["age"].mean())

    return user_count, stats

def generate_single_copy(segment_kr: str, persona_desc: list):
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요."
        return

    client = anthropic.Anthropic(api_key=api_key)
    k = min(len(persona_desc), random.randint(1, 2))
    chosen_traits = random.sample(persona_desc, k)

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=f"""당신은 20~30대 여성에게 인기가 가장 많은 스포츠 웨어 브랜드 'Athlepa'의 마케팅 카피라이터입니다.
        1. [필수] 특정 이름은 언급하지 말고 해당 페르소나 그룹 전체에게 대량 발송할 수 있는 매력적이면서도 보편적인 문장으로 작성하세요.
        2. 이번 메일에서 반영할 소구 포인트는 아래로 한정합니다. 다른 특성은 언급하지 마세요. - {chosen_traits}
        3. 구체적인 숫자, 시간, 혜택 디테일을 반영하여 작성하세요. 
        4. 다음은 참고할 수 있는 실제 프로모션 메일입니다. 절대 똑같이 작성하지 말고 형식만 참고하세요. 형식도 마찬가지로 똑같이 작성하지 마세요.
            제목: 2026년 수디오 여름세일 ☀️ 아직 진행중
            본문: 햇살 가득한 여름, 언제 어디서나 좋아하는
            사운드와 함께하세요. ☀️
            지금 한정 기간 동안 최대 40% OFF

            제목: (광고) 새로운 에코 컬렉션 출시!
            본문: 주변의 시선보다 나만의 방식으로
            LET THEM TALK
            ㅇㅇㅇ과 함께한 새로운 에코 컬렉션을 만나보세요.

            제목: (광고) New Galaxy, 지금 알림 신청해야 하는 이유📌
            본문: 지금 어떤 폰을 사용하시나요?
            Galaxy Unpacked
            This timer has been deactivated.
            곧 새롭게 펼쳐집니다. 

            제목: (광고)저희가 또 해냈습니다!
            본문: 세일
            모든 상품 ₩2,500 이하
            파격 할인을 받으려면 바로 열어보세요

            제목: (광고) 슈퍼 마리오 ™ 컬렉션 출시!
            본문: 파이프 드림
            새로운 슈퍼 마리오™ 컬렉션이 출시되었어요!
            성인부터 키즈, 토들러까지 모두를 위한 다양한 스타일이 준비되어 있어요. 마리오, 요시, 피치 공주 등 인기 캐릭터가 담긴 클래식 클로그와 함께 나만의 플레이어를 선택해보세요.
            자, 출발!
            즐거움을 더해보세요
            요시 클래식 클로그와 함께 나만의 스타일을 완성해보세요.
            요시 캐릭터 디테일과 사과, 요시 알 등의 지비츠™ 참이 더해져 더욱 특별한 매력을 선사합니다.

            제목: ㅇㅇ님, 프로모션이 있어요 🎉
            본문: 이번 주 차량 서비스 이용 시 50%만큼 아껴보세요
            ㅇㅇ님, 이번주에 이용하시는 차량 서비스 10건을 50% 저렴하게 이용하세요. 2026년 7월 27일 AM 12:00까지 유효한 프로모션이 자동으로 계정에 적용되었습니다. 차량 서비스당 최대 NT$80 혜택을 받으세요.

            제목: 따끈따끈한 최대 50% 할인을 받으세요
            본문: 아직 늦지 않았어요!😛 첫 2회 주문 시 최대 50% 절약 혜택을 이용해 보세요. 
            Uber Eats 프로모션 혜택은 원하는 방식으로 자유롭게 사용하실 수 있습니다. 단골 맛집에서 식사를 주문하거나 편의점에서 일주일 치 식료품과 생필품을 주문해 보세요. 어떤 방법으로 사용하시든 프로모션 혜택이 적용되니, 절약이 그 어느 때보다 쉬워진답니다. 
            약관과 수수료가 적용됩니다. 결제 전에 프로모션 코드를 추가하여 할인을 받으세요. 
            xxxd34sfxasdf

            제목: 하나로 충분한 스트릿 아이템🧢
            본문: 시선을 압도하는 스트릿 아이템, 지금 바로 매일 한정 특가로 만나보세요.

            제목: 오후 8시, 크리니크 ~34% 특가💖
            본문: 럭스에딧 3주년 스페셜 라이브! 
            선착순 미니 파우치/브러쉬/클렌징밤 증정까지

            제목: ㅇㅇㅇ 장기 고객님 혜택 안내 드립니다. 
            본문: 뮤지컬 <그날들> 최대 50% 할인

            제목: 쏠쏠한 혜택 [15,000원] 놓치면 안돼요🎉
            본문: 가입과 동시에 드리는 할인 쿠폰!
            어렵지 않아요! 바로 쿠폰 받고 치킨 먹기!🍗

        5. 대량 발송이라는 점을 참고해서 개인화보다는 페르소나 그룹 전체에게 공감될 수 있는 문장으로 작성하세요. 
        6. 주어진 페르소나 그룹 특성에 맞는 이메일을 한국어로 작성하세요.
    형식:
    제목: (15자 내외)
    본문: (2-3문장) (내용에는 '본문:'이라는 라벨 단어를 포함하지 마세요.)""",
        messages=[{
            "role": "user",
            "content": f"""세그먼트: {segment_kr}
    특성: {', '.join(chosen_traits)}

    위 페르소나 그룹의 행동 정보를 자연스럽게 반영해 전체에게 일괄 발송할 이메일 카피를 작성해주세요. """
        }]
    ) as stream:
        for text in stream.text_stream:
            yield text


def _step_indicator(current_step):
    parts = []
    for i, label in enumerate(STEP_LABELS):
        num = i + 1
        done = current_step > num
        active = current_step == num
        circle_cls = "step-circle-active" if (done or active) else "step-circle-pending"
        mark = "✓" if done else str(num)
        label_cls = "step-label-active" if active else ("step-label-done" if done else "step-label-pending")
        parts.append(
            f"<div class='step-item'><span class='step-circle {circle_cls}'>{mark}</span>"
            f"<span class='step-label {label_cls}'>{label}</span></div>"
        )
        if i < len(STEP_LABELS) - 1:
            line_cls = "step-line-done" if current_step > num else "step-line-pending"
            parts.append(f"<div class='step-line {line_cls}'></div>")
    st.markdown(f"<div class='step-indicator'>{''.join(parts)}</div>", unsafe_allow_html=True)


def _init_campaign_state():
    defaults = {
        "campaign_step": 1,
        "selected_segment": list(TARGET_OPTIONS.keys())[0],
        "editable_copy": "",
        "target_count": 0,
        "generated_copy": "",
        "campaign_editing": False,
        "campaign_full_sent": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_step1():
    col_left, col_right = st.columns(2)

    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>캠페인 카피 생성</p>", unsafe_allow_html=True)

            segments = list(TARGET_OPTIONS.keys())
            default_index = segments.index(st.session_state["selected_segment"])

            selected_segment = st.selectbox(
                "타겟 세그먼트",
                options=segments,
                index=default_index,
                help="위 6개는 가입 시점에 정해진 고정 성향, 'RFM:'은 최근 구매 행동 기준으로 매번 다시 계산되는 등급, "
                     "마지막은 장바구니에 담고 구매하지 않은 고객입니다.",
            )
            option_info = TARGET_OPTIONS[selected_segment]
            real_count, _ = get_segment_info(selected_segment)
            st.caption(f"대상 인원: {real_count}명")

            st.session_state["selected_segment"] = selected_segment
            st.session_state["target_count"] = real_count

            generate_btn = st.button(
                "카피 자동 생성", icon=":material/auto_awesome:",
                type="primary", use_container_width=True,
            )

    with col_right:
        with st.container(border=True):
            hcol1, hcol2 = st.columns([3, 1])
            with hcol1:
                st.markdown("<p style='font-size:0.8rem;font-weight:600'>발송 메시지</p>", unsafe_allow_html=True)
            area_placeholder = st.empty()

            if generate_btn:
                full_response = ""
                for chunk in generate_single_copy(selected_segment, option_info["desc"]):
                    full_response += chunk
                    display_text = full_response.replace("본문:", "").strip()
                    area_placeholder.text_area("발송 메시지", value=display_text, height=200, label_visibility="collapsed")
                final_text = full_response.replace("본문:", "").strip()
                st.session_state["editable_copy"] = final_text
                st.session_state["generated_copy"] = final_text
                st.session_state["campaign_editing"] = False
                st.rerun()
            else:
                message = st.session_state.get("editable_copy", "")
                if st.session_state["campaign_editing"]:
                    edited = area_placeholder.text_area(
                        "발송 메시지", value=message, height=200,
                        key="editing_text_area", label_visibility="collapsed",
                    )
                    st.session_state["editable_copy"] = edited
                    st.session_state["generated_copy"] = edited
                else:
                    with area_placeholder:
                        st.markdown(
                            f"<div class='message-preview' style='min-height:170px'>"
                            f"{message if message else '카피 자동 생성 버튼을 누르면 메시지가 표시됩니다.'}</div>",
                            unsafe_allow_html=True,
                        )

            fcol1, fcol2 = st.columns([3, 1])
            with fcol1:
                st.caption(f"{len(st.session_state.get('editable_copy', ''))}자")
            with fcol2:
                if message := st.session_state.get("editable_copy", ""):
                    if st.session_state["campaign_editing"]:
                        if st.button("완료", use_container_width=True, key="btn_edit_done"):
                            st.session_state["campaign_editing"] = False
                            st.rerun()
                    else:
                        if st.button("수정", use_container_width=True, key="btn_edit_start"):
                            st.session_state["campaign_editing"] = True
                            st.rerun()

    bcol1, bcol2 = st.columns(2)
    with bcol2:
        if st.button(
            "다음 단계", icon=":material/arrow_forward:", icon_position="right",
            type="primary", use_container_width=True,
            disabled=not st.session_state.get("editable_copy", "").strip(),
            key="btn_step1_next",
        ):
            st.session_state["campaign_step"] = 2
            st.rerun()


def _render_step2():
    col_left, col_right = st.columns(2)

    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>예약 발송 설정</p>", unsafe_allow_html=True)
            dcol1, dcol2 = st.columns(2)
            with dcol1:
                st.date_input("발송 날짜", value=datetime.now().date(), key="schedule_date")
            with dcol2:
                st.time_input("발송 시간", value=dtime(9, 0), key="schedule_time")

    with col_right:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>메시지 확인</p>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='message-preview'>{st.session_state.get('editable_copy', '')}</div>",
                unsafe_allow_html=True,
            )

    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("이전", use_container_width=True, key="btn_step2_prev"):
            st.session_state["campaign_step"] = 1
            st.rerun()
    with bcol2:
        if st.button(
            "다음 단계", icon=":material/arrow_forward:", icon_position="right",
            type="primary", use_container_width=True, key="btn_step2_next",
        ):
            st.session_state["campaign_step"] = 3
            st.rerun()


def _render_step3():
    segment = st.session_state["selected_segment"]
    count = st.session_state["target_count"]
    message = st.session_state.get("editable_copy", "")
    send_date = st.session_state.get("schedule_date", datetime.now().date())
    send_time = st.session_state.get("schedule_time", dtime(9, 0))

    col_left, col_right = st.columns(2)

    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>발송 요약</p>", unsafe_allow_html=True)
            rows = [
                ("타겟 세그먼트", segment),
                ("대상 인원", f"{count}명"),
                ("발송 채널", "이메일"),
                ("발송 날짜", str(send_date)),
                ("발송 시간", send_time.strftime("%H:%M")),
            ]
            rows_html = "".join(
                f"<div class='summary-row'><span class='summary-label'>{k}</span>"
                f"<span class='summary-value'>{v}</span></div>"
                for k, v in rows
            )
            st.markdown(rows_html, unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>메시지 미리보기</p>", unsafe_allow_html=True)
            st.markdown(f"<div class='message-preview'>{message}</div>", unsafe_allow_html=True)

    with col_right:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>테스트 발송</p>", unsafe_allow_html=True)
            st.caption("실제 발송 전 내용을 확인하세요.")
            test_email = st.text_input("수신 이메일", placeholder="본인 이메일 주소 입력", key="test_email_input", label_visibility="visible")
            if st.button("테스트 발송", use_container_width=True, key="btn_test_send"):
                if not test_email:
                    st.warning("이메일 주소를 입력해주세요.")
                else:
                    try:
                        lines = message.split("\n")
                        subject = lines[0].replace("제목: ", "").strip()
                        body = "\n".join(lines[2:]).replace("본문: ", "").strip()
                        status = send_email(test_email, subject, body)
                        if status == 202:
                            st.markdown(
                                "<div class='confirm-box'><span class='dot'>✓</span>"
                                "<span style='font-size:11px;color:#047857'>테스트 메일이 발송되었습니다.</span></div>",
                                unsafe_allow_html=True,
                            )
                            save_history(segment, message, 1, "테스트 발송")
                        else:
                            st.error(f"발송 실패: SendGrid 응답 코드 {status}")
                    except Exception as e:
                        st.error(f"발송 실패: {e}")

        st.markdown("<div style='height:2px'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("<p style='font-size:0.8rem;font-weight:600'>전체 발송</p>", unsafe_allow_html=True)
            st.caption(f"{count}명 전체에게 발송됩니다. 발송 후 취소가 불가합니다.")

            if st.session_state["campaign_full_sent"]:
                st.markdown(
                    "<div class='confirm-box'><span class='dot'>✓</span>"
                    "<span style='font-size:11px;color:#047857'>발송 완료 — 캠페인이 성공적으로 등록되었습니다.</span></div>",
                    unsafe_allow_html=True,
                )
            else:
                if st.button(f"전체 발송 ({count}명)", icon=":material/rocket_launch:", type="primary", use_container_width=True, key="btn_full_send"):
                    recipients = load_test_recipients()
                    if recipients.empty:
                        st.warning(
                            "data/test_recipients.csv 에 등록된 테스트 수신자가 없습니다. "
                            "name,email 컬럼으로 팀원 이메일을 등록해주세요."
                        )
                    else:
                        target_dt = datetime.combine(send_date, send_time)
                        if KST:
                            target_dt = target_dt.replace(tzinfo=KST)
                        now = datetime.now(KST) if KST else datetime.now()
                        is_future = target_dt > now
                        if is_future and target_dt > now + timedelta(hours=72):
                            st.warning("SendGrid는 최대 72시간 이내 예약만 지원해요. 발송 설정 단계에서 시각을 다시 확인해주세요.")
                        else:
                            send_at_ts = int(target_dt.timestamp()) if is_future else None
                            lines = message.split("\n")
                            subject = lines[0].replace("제목: ", "").strip()
                            body = "\n".join(lines[2:]).replace("본문: ", "").strip()

                            success_count, fail_count, succeeded_emails = 0, 0, []
                            for _, row in recipients.iterrows():
                                try:
                                    status = send_email(row["email"], subject, body, send_at=send_at_ts)
                                    if status == 202:
                                        success_count += 1
                                        succeeded_emails.append(row["email"])
                                    else:
                                        fail_count += 1
                                except Exception as e:
                                    fail_count += 1
                                    st.error(f"{row['email']} 발송 실패: {e}")

                            if success_count:
                                st.session_state["campaign_full_sent"] = True
                                if is_future:
                                    save_history(
                                        segment, message, success_count,
                                        f"예약 등록 완료 ({target_dt.strftime('%Y-%m-%d %H:%M')} SendGrid 자동 발송 예정)"
                                    )
                                    save_scheduled_emails(segment, subject, succeeded_emails, target_dt)
                                else:
                                    save_history(
                                        segment, message, success_count,
                                        f"전체 발송 완료 (테스트 {success_count + fail_count}명 중 {success_count}명 성공)"
                                    )
                                st.rerun()
                            else:
                                st.error("발송에 실패했어요. SendGrid 설정을 확인해주세요.")

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        if st.button("이전 단계", icon=":material/expand_less:", type="tertiary", key="btn_step3_prev"):
            st.session_state["campaign_full_sent"] = False
            st.session_state["campaign_step"] = 2
            st.rerun()


def render_campaign_builder():
    _init_campaign_state()
    _step_indicator(st.session_state["campaign_step"])

    if st.session_state["campaign_step"] == 1:
        _render_step1()
    elif st.session_state["campaign_step"] == 2:
        _render_step2()
    else:
        _render_step3()