"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성
"""

import streamlit as st
import anthropic
from dotenv import load_dotenv
import pandas as pd
from datetime import datetime
import os
import random

load_dotenv()


PERSONAS = {
    "신규 탐색자": {"count": 150, "desc": ["가입 14일 이내", "첫 구매 유도 필요", "신규회원 전용 웰컴 혜택", "베스트셀러 추천"]},
    "충동 구매자": {"count": 200, "desc": ["고빈도 중액", "신상품·기획전 반응 높음", "시각적 매력", "매진 임박", "최신 트렌드", "직관성", "긴박감"]},
    "할인 구매자": {"count": 200, "desc": ["세일 시즌에만 반응", "할인 쿠폰이나 적립금 효과적", "할인율이나 할인 금액 명확히 보여주고 강조", "쿠폰 만료 임박 강조", "가성비", "긴박감"]},
    "브랜드 충성 고객": {"count": 150, "desc": ["중빈도 재구매", "VIP 혜택 선호", "브랜드에 대한 신뢰도 높음", "신제품 사전 공개"]},
    "이탈 위험 고객": {"count": 150, "desc": ["45일 이상 미구매", "재활성화 필요", "호기심 자극하는 메일 제목", "장바구니 상품 리마인드", "파격적인 리텐션 쿠폰이나 혜택", "놓치기 아쉬운 혜택 강조"]},
    "휴면 고객": {"count": 150, "desc": ["90일 이상 미방문", "윈백 캠페인 대상", "다시 돌아오면 제공되는 혜택", "강렬한 한 줄 소구로 극적인 이메일 제목"]},
}

PERSONA_MAP = {
    "신규 탐색자": "new_explorer",
    "충동 구매자": "impulsive_buyer",
    "할인 구매자": "discount_hunter",
    "브랜드 충성 고객": "brand_loyalist", 
    "이탈 위험 고객": "churn_risk",
    "휴면 고객": "dormant"
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


def get_segment_info(persona_kr: str):
    persona_en = PERSONA_MAP.get(persona_kr)
    df_users, df_orders, df_events = load_all_datasets()
    
    if df_users.empty:
        return PERSONAS.get(persona_kr, {}).get("count", 0), {}

    if 'persona_type' not in df_users.columns:
        df_users = classify_persona(df_users, df_orders)

    target_users = df_users[df_users['persona_type'] == persona_en]
    user_count = len(target_users)
    
    if user_count == 0:
        return PERSONAS.get(persona_kr, {}).get("count", 0), {}

    stats = {}
    if 'age' in target_users.columns:
        stats['avg_age'] = int(target_users['age'].mean())
    if not df_orders.empty and 'category' in df_orders.columns:
        merged_orders = pd.merge(target_users[['user_id']], df_orders, on='user_id', how='inner')
        if not merged_orders.empty:
            stats['top_category'] = merged_orders['category'].mode()[0]

    return user_count, stats

def generate_single_copy(segment_kr: str, persona_desc: list, stats: dict = None):
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield "⚠️ ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요."
        return

    client = anthropic.Anthropic(api_key=api_key)
    k = min(len(persona_desc), random.randint(1, 2))
    chosen_traits = random.sample(persona_desc, k)

    target_info = f"소구 포인트: {chosen_traits}"
    if stats and 'top_category' in stats:
        target_info += f" | 해당 세그먼트 선호 카테고리: [{stats['top_category']}]"

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


def render_campaign_builder():
    st.subheader("📋 캠페인 카피 생성")

    if "selected_segment" not in st.session_state:
        st.session_state["selected_segment"] = list(PERSONAS.keys())[0]
    if "editable_copy" not in st.session_state:
        st.session_state["editable_copy"] = ""
    if "target_count" not in st.session_state:
        st.session_state["target_count"] = PERSONAS[st.session_state["selected_segment"]]["count"]
    if "generated_copy" not in st.session_state:
        st.session_state["generated_copy"] = ""

    col1, col2 = st.columns(2)

    with col1:
        segments = list(PERSONAS.keys())
        default_segment = st.session_state.get("recommended_segment", st.session_state["selected_segment"])

        default_index = 0
        if default_segment in segments:
            default_index = segments.index(default_segment)

        selected_segment = st.selectbox(
            "타겟 세그먼트",
            options=segments,
            index=default_index
        )
        
        persona_info = PERSONAS[selected_segment]
        real_count, stats = get_segment_info(selected_segment)

        caption_msg = f"대상 인원: {real_count}명 | {persona_info['desc']}"
        if 'top_category' in stats:
            caption_msg += f" | 선호 상품: {stats['top_category']}"
        st.caption(caption_msg)

        st.session_state["selected_segment"] = selected_segment  # 💡 세렉트박스 선택 시 실시간으로 세션 업데이트
        st.session_state["target_count"] = real_count

    with col2:
        st.text_input("발송 채널", value="이메일 (SendGrid)", disabled=True)

    generate_btn = st.button("✨ 카피 자동 생성", type="primary", use_container_width=True)

    st.markdown("---")
    area_placeholder = st.empty()

    # 1. 버튼 클릭 시: 입력창 안에서 실시간으로 생성
    if generate_btn:
        full_response = ""
        for chunk in generate_single_copy(selected_segment, persona_info['desc'], stats):
            full_response += chunk
            display_text = full_response.replace("본문:", "").strip()
            
            # 실시간으로 text_area 내부에 출력
            area_placeholder.text_area(
                label="발송 메시지",
                value=display_text,
                height=220,
            )

        # 생성 완료 후 데이터 저장 및 깔끔하게 화면 갱신
        final_text = full_response.replace("본문:", "").strip()
        st.session_state["editable_copy"] = final_text
        st.session_state["generated_copy"] = final_text
        st.session_state["editing_text_area"] = final_text

        st.rerun()

    # 2. 평소 상태: 수정 가능한 입력창 단 하나만 표시
    else:
        edited_message = area_placeholder.text_area(
            label="발송 메시지",
            value=st.session_state.get("editable_copy", ""),
            height=220,
            key="editing_text_area"
        )

        st.session_state["editable_copy"] = edited_message
        st.session_state["generated_copy"] = edited_message