"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성
"""

import streamlit as st
import anthropic
from dotenv import load_dotenv
import pandas as pd
import os
import random

# ★ 1. 페이지 설정은 무조건 코드 최상단에 배치
st.set_page_config(
    page_title="AI CRM Dashboard",
    page_icon="📊",
    layout="wide"
)

load_dotenv()

# ==========================================
# [안전성을 강화한 데이터 로딩 함수]
# ==========================================

def get_customer_from_events(target_user_id=None):
    """경로 에러 방지를 위해 상대 경로 및 예외 처리 적용"""
    # 1. 상대 경로를 기본으로 설정 (동일 폴더 또는 data 폴더 탐색)
    possible_user_paths = ["users.csv", "data/users.csv"]
    user_csv_path = None
    for p in possible_user_paths:
        if os.path.exists(p):
            user_csv_path = p
            break

    real_name = None
    if user_csv_path:
        try: 
            df_users = pd.read_csv(user_csv_path)
            df_users['user_id'] = df_users['user_id'].astype(str)
           
            if target_user_id:
                str_target_id = str(target_user_id).strip()
                user_match = df_users[
                    (df_users['user_id'] == str_target_id) |
                    (df_users['user_id'].str.startswith(str_target_id))
                ]
                if not user_match.empty and 'name' in user_match.columns:
                    real_name = user_match.iloc[0]['name']
            else:
                if not df_users.empty and 'name' in df_users.columns:
                    real_name = df_users.iloc[0]['name']
                    target_user_id = df_users.iloc[0]['user_id']
        except Exception:
            pass



    possible_paths = [
        "events.csv",
        "data/events.csv"
    ]
    
    csv_path = None
    for p in possible_paths:
        if os.path.exists(p):
            csv_path = p
            break

    # 파일이 없는 경우 기본 더미 데이터 반환 (화면 다운 방지)
    if not csv_path:
        return {
            'name': real_name,
            'recently_viewed': '아우터 (상품 ID: 6071ce)',
            'cart_items': ['레깅스(1a330a)', '조거팬츠(7b65c9)'],
            'recent_purchase': '스윔웨어 (상품 ID: 99eaa9)'
        }

    try:
        df = pd.read_csv(csv_path)
        if not target_user_id and not df.empty:
            target_user_id = df['user_id'].mode()[0]

        user_df = df[df['user_id'] == target_user_id].sort_values('timestamp', ascending=False)

        if user_df.empty:
            return {'name': str(target_user_id), 'recently_viewed': '없음', 'cart_items': [], 'recent_purchase': '없음'}

        views = user_df[user_df['event_type'] == 'product_view']
        recently_viewed = f"{views.iloc[0]['category']} (상품 ID: {views.iloc[0]['product_id']})" if not views.empty else '없음'

        carts = user_df[user_df['event_type'] == 'add_to_cart']
        cart_items = [f"{row['category']}({row['product_id']})" for _, row in carts.head(3).iterrows()] if not carts.empty else []

        purchases = user_df[user_df['event_type'] == 'purchase']
        recent_purchase = f"{purchases.iloc[0]['category']} (상품 ID: {purchases.iloc[0]['product_id']})" if not purchases.empty else '없음'

        return {
            'name': f"({real_name})",
            'recently_viewed': recently_viewed,
            'cart_items': cart_items,
            'recent_purchase': recent_purchase
        }
    except Exception as e:
        return {'name': '고객님', 'recently_viewed': '없음', 'cart_items': [], 'recent_purchase': '없음'}


PERSONAS = {
    "신규 탐색자": {"count": 150, "desc": ["가입 14일 이내", "첫 구매 유도 필요", "신규회원 전용 웰컴 혜택", "베스트셀러 추천"]},
    "충동 구매자": {"count": 200, "desc": ["고빈도 중액", "신상품·기획전 반응 높음", "시각적 매력", "희소성 있는 한정판", "매진 임박", "최신 트렌드", "직관성", "긴박감"]},
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

def get_customers_by_persona(persona_kr, users_csv="users.csv"): 
    """선택한 페르소나에 속한 전체 고객 목록 추출"""
    persona_en = PERSONA_MAP.get(persona_kr)
    if os.path.exists(users_csv):
        df_users = pd.read_csv(users_csv)
        filtered = df_users[df_users['persona_type'] == persona_en]
        return filtered.to_dict('records')
    return []

def generate_single_copy(segment_kr: str, persona_desc: list, customer_info: dict):
    """고객 각각에 대한 개별 카피 생성"""
    
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield "⚠️ ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요."
        return

    client = anthropic.Anthropic(api_key=api_key)

    k = min(len(persona_desc), random.randint(1, 2))
    chosen_traits = random.sample(persona_desc, k)
    customer_name = customer_info.get('name', '고객님')

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=f"""당신은 20~30대 여성에게 인기가 가장 많은 스포츠 웨어 브랜드의 마케팅 카피라이터입니다.
        1. [필수] 이메일 첫 줄에 반드시 고객 이름("{customer_name}")을 자연스럽게 언급하세요.
        2. 이번 메일에서 반영할 소구 포인트는 아래로 한정합니다. 다른 특성은 언급하지 마세요. - {chosen_traits}
        3. 구체적인 숫자, 시간, 혜택 디테일을 반영하여 작성하세요.
        4. 아래 고객 데이터 중 최소 1개 이상을 본문에 자연스럽게 포함시켜 이 사람만을 위한 메일인 것처럼 작성하세요. 
        5. 주어진 고객 세그먼트 특성에 맞는 개인화 이메일을 한국어로 작성하세요.
    형식:
    제목: (15자 내외)
    본문: (2-3문장)""",
        messages=[{
            "role": "user",
            "content": f"""세그먼트: {segment_kr}
    고객 이름: {customer_name}
    최근 열람 상품: {customer_info.get('recently_viewed', '없음')}
    장바구니: {', '.join(customer_info.get('cart_items', [])) if customer_info.get('cart_items') else '없음'}
    최근 구매: {customer_info.get('recent_purchase', '없음')}
    특성: {', '.join(chosen_traits)}

    위 {customer_name}의 행동 정보를 자연스럽게 반영해 맞춤형 이메일을 작성해주세요. """
        }]
    ) as stream:
        for text in stream.text_stream:
            yield text


def render_campaign_builder():
    st.subheader("📋 캠페인 카피 생성")

    col1, col2 = st.columns(2)

    with col1:
        segments = list(PERSONAS.keys())
        default_segment = st.session_state.get("recommended_segment")

        default_index = 0
        if default_segment in segments:
            default_index = segments.index(default_segment)

        selected_segment = st.selectbox(
            "타겟 세그먼트",
            options=segments,
            index=default_index
        )
        
        persona_info = PERSONAS[selected_segment]
        st.caption(f"대상 인원: {persona_info['count']}명 | {persona_info['desc']}")

    with col2:
        st.text_input("발송 채널", value="이메일 (SendGrid)", disabled=True)

    generate_btn = st.button("✨카피 자동 생성", type="primary")

    if generate_btn:
        st.subheader("생성된 메시지")
        
        # 1. users.csv에서 선택한 세그먼트(페르소나)에 맞는 유저 목록 가져오기
        selected_user_id = None
        try:
            user_path = "users.csv" if os.path.exists("users.csv") else "data/users.csv"
            if os.path.exists(user_path):
                df_u = pd.read_csv(user_path)
                
                # 선택된 세그먼트로 유저 필터링
                filtered_users = df_u[df_u['persona_type'] == selected_segment]
                
                # 해당 세그먼트 유저 중 1명을 무작위(랜덤) 추출!
                if not filtered_users.empty:
                    selected_user_id = random.choice(filtered_users['user_id'].tolist())
                else:
                    selected_user_id = random.choice(df_u['user_id'].tolist())
        except Exception as e:
            pass

        # 2. ★ 추출한 랜덤 target_user_id를 넘겨서 해당 고객의 실제 정보 가져오기
        customer_data = get_customer_from_events(target_user_id=selected_user_id)

        # 3. Claude 카피 생성
        with st.spinner("Claude가 카피를 작성하고 있습니다..."):
            full_response = ""
            placeholder = st.empty()

            for chunk in generate_single_copy(selected_segment, persona_info['desc'], customer_data):
                full_response += chunk
                placeholder.markdown(full_response + "▌")

            placeholder.markdown(full_response)

        st.session_state["generated_copy"] = full_response
        st.session_state["selected_segment"] = selected_segment
        st.session_state["target_count"] = persona_info["count"]


# ==========================================
# [메인 대시보드 UI 및 네비게이션]
# ==========================================

st.title("📊 AI CRM Dashboard")
st.markdown("---")

st.sidebar.title("사이드바")
menu = st.sidebar.radio(
    "이동할 페이지를 선택하세요.",
    ["대시보드", "캠페인 자동화"]
)

if menu == "대시보드":
    st.header("🎯 CRM 마케팅 대시보드 홈")
    st.write("오신 것을 환영합니다! 왼쪽 사이드바 메뉴에서 원하는 기능을 선택해 보세요.")
    
    st.markdown("""
    ### 안녕하세요! AI CRM 대시보드입니다.

    왼쪽 사이드바에서 페이지를 선택하세요.

    - 📊 **대시보드**: 고객 분석 및 AI 인사이트
    - 🤖 **캠페인 자동화**: AI 기반 이메일 자동화
    """)

elif menu == "캠페인 자동화":
    render_campaign_builder()