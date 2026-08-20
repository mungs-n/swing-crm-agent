"""
담당자: 탭2 담당자 A
작업 내용: 세그먼트 분류, 캠페인 설정 UI, Claude 카피 생성 및 멀티 채널(카톡/문자/푸시/메일) 발송 연동
"""

import streamlit as st
import anthropic
from dotenv import load_dotenv
import pandas as pd
from datetime import datetime, time as dtime, timedelta
import os
import random
import uuid
import html
import base64
import re
import streamlit.components.v1 as components

from utils.rfm import calculate_rfm, assign_segment
from automation.email_sender import (
    send_email,
    save_history,
    save_scheduled_emails,
    load_test_recipients,
    record_campaign_send,
    KST,
    REPEATING_FREQS,
    WEEKDAY_LABELS,
    register_recurring_campaign,
)

load_dotenv()

STEP_LABELS = ["타겟/채널 선택 & 카피 생성", "발송 방식 설정", "최종 확인 & 발송"]

# 타겟 옵션 정의
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

# ==============================================================================
# 멀티 채널 API 발송 더미(Stub) 함수
# ==============================================================================

def send_kakaotalk(receiver_info: str, message: str, image=None, send_at=None) -> int:
    return 202

def send_sms_lms(receiver_info: str, message: str, image=None, send_at=None) -> int:
    return 202

import firebase_admin
from firebase_admin import credentials, messaging

if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred)
    except Exception:
        pass

def send_web_push(receiver_info: str, message: str, image=None, send_at=None) -> int:
    try:
        msg = messaging.Message(
            notification=messaging.Notification(
                title="Athlepa 알림",
                body=message,
                image=image
            ),
            token=receiver_info,
        )
        messaging.send(msg)
        return 200
    except Exception as e:
        print("FCM 발송 실패:", e)
        return 500

def send_campaign_message(channel: str, receiver_info: str, subject: str, body: str, image=None, send_at=None, send_id=None) -> int:
    if "카카오톡" in channel:
        full_msg = f"[{subject}]\n\n{body}" if subject else body
        return send_kakaotalk(receiver_info, full_msg, image=image, send_at=send_at)
    elif "문자" in channel:
        full_msg = f"[{subject}]\n\n{body}" if subject else body
        return send_sms_lms(receiver_info, full_msg, image=image, send_at=send_at)
    elif "웹 푸시" in channel:
        full_msg = f"{subject}\n{body}" if subject else body
        return send_web_push(receiver_info, full_msg, image=image, send_at=send_at)
    else:
        # send_id는 SendGrid custom_arg로 실려서, 오픈/클릭 웹훅이 어느 campaign_sends
        # 행을 업데이트할지 찾는 데 쓰인다 (이메일 채널에서만 의미가 있다).
        return send_email(receiver_info, subject, body, send_at=send_at, send_id=send_id)

# ==============================================================================

def parse_message_text(raw_text: str):
    if not raw_text:
        return "", ""
    
    lines = raw_text.strip().split("\n")
    subject = ""
    body_lines = []

    for line in lines:
        clean_line = line.strip()
        if clean_line.startswith("제목:"):
            subject = clean_line.replace("제목:", "").strip()
        elif clean_line.startswith("본문:"):
            body_lines.append(clean_line.replace("본문:", "").strip())
        else:
            body_lines.append(clean_line)

    body = "\n".join(body_lines).strip()
    return subject, body


def classify_persona(df_users: pd.DataFrame, df_orders: pd.DataFrame = None) -> pd.DataFrame:
    df = df_users.copy()
    if df_orders is not None and not df_orders.empty:
        latest_orders = df_orders.groupby('user_id').agg(
            last_order_date=('order_date', 'max'),
            coupon_count=('coupon_used', lambda x: x.sum() if x.dtype == 'bool' or x.dtype == 'int64' else 0),
            avg_discount=('discount_amount', 'mean')
        ).reset_index()
        df = pd.merge(df, latest_orders, on='user_id', how='left')
    
    today = pd.to_datetime('today')
    df['signup_date'] = pd.to_datetime(df['signup_date']) if 'signup_date' in df.columns else today
    df['days_since_signup'] = (today - df['signup_date']).dt.days

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
    if df_orders.empty:
        return pd.Series(dtype=object)
    rfm = assign_segment(calculate_rfm(df_orders.copy()))
    return rfm.loc[rfm["segment"] == tier, "user_id"]


def _cart_abandon_ids(df_events: pd.DataFrame) -> set:
    if df_events.empty or "event_type" not in df_events.columns:
        return set()
    cart_users = set(df_events.loc[df_events["event_type"] == "add_to_cart", "user_id"])
    purchase_users = set(df_events.loc[df_events["event_type"] == "purchase", "user_id"])
    return cart_users - purchase_users


def get_segment_info(segment_kr: str):
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


def generate_single_copy(segment_kr: str, persona_desc: list, channel: str = "카카오톡"):
    channel_guides = {
        "카카오톡": "카카오톡 메시지 형식으로 친근하고 가독성 있게 작성하세요. 줄바꿈을 적극적으로 활용해 가독성을 높이고, 하단에 행동 유도 버튼 문구를 포함하세요.",
        "문자(SMS/LMS)": "문자 메시지(SMS/LMS) 형식으로 작성하세요. 99바이트 이내의 짧고 명확한 핵심 위주 문장으로 작성하며 핵심 혜택을 직관적으로 보여주세요.",
        "웹 푸시": "웹 브라우저 푸시 알림 형태입니다. 제목은 15자 이내로 극적인 호기심을 유도하고, 본문은 30자 이내의 아주 단문으로 클릭을 유도하도록 작성하세요.",
        "이메일": "이메일 형식으로 클릭을 부르는 서브젝트(제목)와 2~3문장 본문으로 구성하세요. 구체적인 혜택 내용과 Call to Action(행동 유도)을 명확하게 반영하세요.",
    }

    selected_guide = channel_guides.get(channel, channel_guides["카카오톡"])
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        yield "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요."
        return

    k = min(len(persona_desc), random.randint(1, 2)) if persona_desc else 0
    chosen_traits_list = random.sample(persona_desc, k) if k > 0 else ["일반 특성"]
    chosen_traits_str = ", ".join(chosen_traits_list)

    system_prompt = f"""당신은 스포츠 웨어 브랜드 'Athlepa'의 마케팅 카피라이터입니다.
현재 선택된 발송 채널: {channel}
[채널별 핵심 작성 가이드]: {selected_guide}

작성 규칙:
1. [필수] 특정 개인 이름을 언급하지 말고 해당 타겟 그룹 전체에게 대량 발송할 수 있는 매력적인 문장으로 작성하세요.
2. [필수] 마크다운 문법(##, ---, **, *, ` 등)을 절대로 사용하지 마세요. 순수 텍스트로만 작성하세요.
3. 이번 메시지에서 반영할 소구 포인트: {chosen_traits_str}
4. 지정된 채널({channel})의 특성과 포맷 규칙을 엄격하게 준수하세요.

형식:
제목: (채널 특성에 맞춘 제목)
본문: (채널 특성에 맞춘 본문)"""

    client = anthropic.Anthropic(api_key=api_key)

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": f"세그먼트: {segment_kr}\n특성: {chosen_traits_str}\n발송 채널: {channel}\n\n위 조건에 맞춰 최적화된 마케팅 메시지를 작성해주세요."
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
        # 완료된 단계는 원 안의 텍스트 대신 색상(step-circle-active)으로 구분한다.
        mark = str(num)
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
        "selected_channel": "카카오톡",
        "editable_copy": "",
        "target_count": 0,
        "generated_copy": "",
        "campaign_editing": False,
        "campaign_full_sent": False,
        "campaign_image": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def render_channel_preview(channel: str, message: str, image_file=None):
    if not message:
        message = "작성된 메시지가 없습니다."

    clean_text = re.sub(r'#{1,6}\s*', '', message)
    clean_text = re.sub(r'^\s*[-*_]{3,}\s*$', '', clean_text, flags=re.MULTILINE)
    clean_text = re.sub(r'\*\*(.*?)\*\*', r'\1', clean_text)

    clean_message = html.escape(clean_text)
    subject, body = parse_message_text(clean_message)
    body_content = body.replace('\n', '<br/>')

    img_tag = ""
    if image_file:
        try:
            image_file.seek(0)
            encoded = base64.b64encode(image_file.read()).decode("utf-8")
            mime_type = image_file.type or "image/png"
            img_tag = f'<img src="data:{mime_type};base64,{encoded}" style="width:100%; border-radius:12px; margin-top:8px; display:block;" />'
        except Exception:
            img_tag = ""

    if "웹 푸시" in channel:
        return f"""
        <div style="background:#1e1e1e; padding:20px 12px; border-radius:24px; max-width:340px; margin:0 auto; box-shadow:0 10px 25px rgba(0,0,0,0.3); font-family:-apple-system, sans-serif;">
            <div style="background:#f6f6f6; border-radius:16px; padding:14px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <div style="display:flex; align-items:center; gap:6px;">
                        <span style="background:#4285f4; width:14px; height:14px; border-radius:4px; display:inline-block;"></span>
                        <span style="font-size:12px; font-weight:600; color:#333;">Chrome</span>
                    </div>
                    <span style="font-size:11px; color:#888;">지금</span>
                </div>
                <div style="font-size:14px; font-weight:700; color:#111; margin-bottom:4px;">{subject or '제목입니다.'}</div>
                <div style="font-size:13px; color:#444; line-height:1.4;">{body_content or '내용입니다.'}</div>
                {img_tag}
            </div>
        </div>"""

    elif "카카오톡" in channel:
        return f"""
        <div style="background:#abc0d0; padding:16px 10px; border-radius:24px; max-width:340px; margin:0 auto; font-family:-apple-system, sans-serif;">
            <div style="background:#ffffff; border-radius:12px 12px 0 0; padding:10px 14px; display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #f0f0f0;">
                <span style="font-size:13px; font-weight:bold; color:#191919;">Athlepa </span>
            </div>
            <div style="background:#ffffff; border-radius:0 0 16px 16px; overflow:hidden; padding:14px;">
                <div style="font-size:15px; font-weight:bold; color:#111; margin-bottom:8px;">{subject or '알림'}</div>
                {img_tag}
                <div style="font-size:13px; color:#333; line-height:1.5; margin-top:8px;">{body_content}</div>
            </div>
        </div>"""

    elif "문자" in channel:
        return f"""
        <div style="background:#f2f2f7; padding:16px 10px; border-radius:24px; max-width:340px; margin:0 auto; font-family:-apple-system, sans-serif;">
            <div style="background:#e9e9eb; border-radius:16px; padding:12px; font-size:13px; color:#000;">
                {f'<div style="font-weight:bold; margin-bottom:6px;">(광고) {subject}</div>' if subject else ''}
                {img_tag}
                <div style="margin-top:6px;">{body_content}</div>
            </div>
        </div>"""

    else:
        return f"""
        <div style="background:#ffffff; border:1px solid #e0e0e0; border-radius:20px; max-width:340px; margin:0 auto; padding:16px; font-family:-apple-system, sans-serif;">
            <div style="font-size:15px; font-weight:bold; color:#111; margin-bottom:10px;">{subject or '제목 없음'}</div>
            {img_tag}
            <div style="font-size:13px; color:#333; line-height:1.5; margin-top:10px;">{body_content}</div>
        </div>"""


def inject_custom_css():
    """요소 겹침을 방지하는 안정적인 레이아웃 CSS 스타일"""
    st.markdown(
        """
        <style>
        .message-preview {
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            padding: 12px;
            font-size: 0.9rem;
            line-height: 1.5;
            color: #495057;
            max-height: 240px;
            min-height: 120px;
            overflow-y: auto;
            white-space: pre-wrap;
            margin-bottom: 12px;
            word-break: break-word;
        }

        .summary-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
            font-size: 0.85rem;
        }
        .summary-label {
            color: #666;
            font-weight: 500;
        }
        .summary-value {
            color: #111;
            font-weight: 600;
        }
        .confirm-box {
            background: #ecfdf5;
            border: 1px solid #a7f3d0;
            padding: 10px;
            border-radius: 8px;
            margin-top: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .confirm-box .dot {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #047857;
            flex-shrink: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_step1():
    col_left, col_right = st.columns(2)

    # --- 왼쪽: 옵션 설정 ---
    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom: 12px;'>1. 타겟 및 채널 선택</p>", unsafe_allow_html=True)

            segments = list(TARGET_OPTIONS.keys())
            default_index = segments.index(st.session_state["selected_segment"])

            selected_segment = st.selectbox(
                "타겟 세그먼트",
                options=segments,
                index=default_index,
                help="가입 성향, RFM 등급, 장바구니 이탈 등 발송 대상을 선택합니다.",
            )
            st.session_state["selected_segment"] = selected_segment
            
            option_info = TARGET_OPTIONS[selected_segment]
            real_count, _ = get_segment_info(selected_segment)
            st.session_state["target_count"] = real_count
            st.caption(f"대상 인원: **{real_count}명**")

            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

            channel_options = ["카카오톡", "문자(SMS/LMS)", "웹 푸시", "이메일"]
            ch_index = channel_options.index(st.session_state.get("selected_channel", "카카오톡"))
            selected_channel = st.radio(
                "발송 채널 선택",
                options=channel_options,
                index=ch_index,
                help="선택한 채널의 형식과 규격에 맞춰 Claude가 카피를 생성합니다."
            )
            st.session_state["selected_channel"] = selected_channel

            st.markdown("<div style='margin-top: 16px;'></div>", unsafe_allow_html=True)

            gen_col1, gen_col2 = st.columns(2)
            with gen_col1:
                generate_btn = st.button(
                    "AI 카피 자동 생성",
                    type="primary",
                    use_container_width=True,
                    key="btn_generate_copy"
                )
            with gen_col2:
                direct_write_btn = st.button(
                    "직접 작성하기",
                    type="secondary",
                    use_container_width=True,
                    key="btn_direct_write"
                )

    # --- 오른쪽: 카피 결과 ---
    with col_right:
        with st.container(border=True):
            st.markdown(f"<p style='font-size:0.95rem; font-weight:700; margin-bottom: 12px;'>생성된 메시지 ({selected_channel})</p>", unsafe_allow_html=True)
            
            area_placeholder = st.empty()

            if direct_write_btn:
                st.session_state["campaign_editing"] = True
                st.rerun()

            if generate_btn:
                full_response = ""
                for chunk in generate_single_copy(selected_segment, option_info["desc"], channel=selected_channel):
                    full_response += chunk
                    display_text = full_response.replace("본문:", "").strip()
                    area_placeholder.markdown(
                        f"<div class='message-preview'>{display_text}</div>",
                        unsafe_allow_html=True
                    )
                
                final_text = full_response.replace("본문:", "").strip()
                st.session_state["editable_copy"] = final_text
                st.session_state["generated_copy"] = final_text
                st.session_state["campaign_editing"] = False
                st.rerun()
            else:
                message = st.session_state.get("editable_copy", "")
                if st.session_state["campaign_editing"]:
                    st.caption("'제목: ...' / '본문: ...' 형식으로 작성하면 채널 형식에 맞춰 자동으로 반영됩니다.")
                    edited = area_placeholder.text_area(
                        "발송 메시지", 
                        value=message, 
                        height=180,
                        key="streaming_text_area_input",
                        label_visibility="collapsed",
                    )
                    st.session_state["editable_copy"] = edited
                    st.session_state["generated_copy"] = edited
                else:
                    with area_placeholder:
                        st.markdown(
                            f"<div class='message-preview'>"
                            f"{message if message else '세그먼트와 채널을 고른 후 [AI 카피 자동 생성]을 누르거나, [직접 작성하기]로 메시지를 바로 작성할 수 있습니다.'}</div>",
                            unsafe_allow_html=True,
                        )

            fcol1, fcol2 = st.columns([3, 1])
            with fcol1:
                st.caption(f"글자 수: {len(st.session_state.get('editable_copy', ''))}자")
            with fcol2:
                message = st.session_state.get("editable_copy", "")
                if message and st.session_state["campaign_editing"]:
                    if st.button("완료", use_container_width=True, key="btn_edit_done"):
                        st.session_state["campaign_editing"] = False
                        st.rerun()
                elif message and not st.session_state["campaign_editing"]:
                    if st.button("수정", use_container_width=True, key="btn_edit_start"):
                        st.session_state["campaign_editing"] = True
                        st.rerun()

            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)

            if st.button(
                "다음 단계 (발송 방식 설정)", 
                type="primary", 
                use_container_width=True,
                disabled=not st.session_state.get("editable_copy", "").strip(),
                key="btn_step1_next",
            ):
                st.session_state["campaign_step"] = 2
                st.rerun()


def _render_step2():
    col_left, col_right = st.columns([1, 1])

    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>2. 발송 세부 설정</p>", unsafe_allow_html=True)
            
            trigger_type = st.selectbox("발송 방식", ["스케줄 기반", "이벤트 트리거 기반", "API 트리거 기반"])
            
            if trigger_type == "스케줄 기반":
                send_freq = st.selectbox(
                    "발송 빈도",
                    ["즉시 발송", "1회 발송", "매일 발송", "3일마다", "일주일마다", "특정 요일 반복"],
                    key="schedule_send_freq",
                )

                if send_freq == "즉시 발송":
                    st.caption("전체 발송 버튼을 누르는 즉시 발송됩니다.")
                    st.session_state["schedule_date"] = datetime.now().date()
                    st.session_state["schedule_time"] = datetime.now().time()
                else:
                    dcol1, dcol2 = st.columns(2)
                    with dcol1:
                        st.date_input(
                            "발송 날짜",
                            value=st.session_state.get("schedule_date", datetime.now().date()),
                            key="schedule_date",
                        )
                    with dcol2:
                        st.time_input(
                            "발송 시간",
                            value=st.session_state.get("schedule_time", dtime(9, 0)),
                            step=timedelta(minutes=5),
                            key="schedule_time",
                        )

                    if send_freq in ("매일 발송", "3일마다", "일주일마다"):
                        interval_label = {"매일 발송": "매일", "3일마다": "3일", "일주일마다": "일주일"}[send_freq]
                        st.caption(f"선택한 날짜부터 {interval_label} 간격으로 반복 발송됩니다.")

                    elif send_freq == "특정 요일 반복":
                        selected_labels = st.multiselect(
                            "반복 요일 선택",
                            options=WEEKDAY_LABELS,
                            default=[
                                WEEKDAY_LABELS[i]
                                for i in st.session_state.get("schedule_weekdays", [])
                                if 0 <= i < len(WEEKDAY_LABELS)
                            ],
                            key="schedule_weekday_labels",
                            help="선택한 요일마다, 설정한 시간에 반복 발송됩니다.",
                        )
                        st.session_state["schedule_weekdays"] = [
                            WEEKDAY_LABELS.index(label) for label in selected_labels
                        ]
                        if not selected_labels:
                            st.caption("반복할 요일을 1개 이상 선택해주세요.")
                        else:
                            st.caption(f"매주 {', '.join(selected_labels)}요일마다 반복 발송됩니다.")
            elif trigger_type == "이벤트 트리거 기반":
                st.selectbox(
                    "트리거 이벤트",
                    [
                        "장바구니 담기 후 미구매",
                        "위시리스트 등록 후 미구매",
                        "회원가입 완료",
                        "첫 구매 완료",
                        "재구매 주기 도래",
                        "리뷰 작성 요청 (배송 완료 후)",
                        "생일/기념일",
                        "회원 등급 승급",
                        "포인트 소멸 임박",
                        "관심 상품 재입고",
                        "관심 상품 가격 인하",
                        "장기 미접속 (휴면 전환 예정)",
                    ],
                )
            else:
                st.text_input("API Endpoint Key", value="api_v1_campaign_trigger")

            st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)
            uploaded_image = st.file_uploader("메시지 첨부 이미지 (선택)", type=["jpg", "png", "jpeg"])
            if uploaded_image:
                st.session_state["campaign_image"] = uploaded_image

    with col_right:
        with st.container(border=True):
            channel = st.session_state.get("selected_channel", "카카오톡")
            st.markdown(f"<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>실시간 미리보기 ({channel})</p>", unsafe_allow_html=True)
            
            message = st.session_state.get("editable_copy", "")
            img = st.session_state.get("campaign_image")
            
            preview_html = render_channel_preview(channel, message, image_file=img)
            components.html(preview_html, height=430, scrolling=True)

    st.markdown("<div style='margin-top: 16px;'></div>", unsafe_allow_html=True)
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        if st.button("이전 단계", use_container_width=True, key="btn_step2_prev"):
            st.session_state["campaign_step"] = 1
            st.rerun()
    with bcol2:
        if st.button("다음 단계 (최종 확인)", type="primary", use_container_width=True, key="btn_step2_next"):
            st.session_state["campaign_step"] = 3
            st.rerun()


def _render_step3():
    segment = st.session_state["selected_segment"]
    channel = st.session_state.get("selected_channel", "카카오톡")
    count = st.session_state["target_count"]
    message = st.session_state.get("editable_copy", "")
    send_date = st.session_state.get("schedule_date", datetime.now().date())
    send_time = st.session_state.get("schedule_time", dtime(9, 0))
    img = st.session_state.get("campaign_image")

    col_left, col_right = st.columns(2)

    with col_left:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>발송 요약</p>", unsafe_allow_html=True)
            freq_display = st.session_state.get("schedule_send_freq", "즉시 발송")
            if freq_display == "특정 요일 반복":
                weekday_idxs = st.session_state.get("schedule_weekdays", [])
                weekday_names = ", ".join(WEEKDAY_LABELS[i] for i in weekday_idxs) or "미선택"
                freq_display = f"특정 요일 반복 ({weekday_names})"

            rows = [
                ("타겟 세그먼트", segment),
                ("발송 채널", channel),
                ("대상 인원", f"{count}명"),
                ("발송 빈도", freq_display),
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
            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>메시지 내용</p>", unsafe_allow_html=True)
            st.markdown(f"<div class='message-preview'>{message}</div>", unsafe_allow_html=True)

    with col_right:
        with st.container(border=True):
            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>테스트 발송</p>", unsafe_allow_html=True)
            st.caption("실제 발송 전 테스트 수신자로 발송해 보세요.")
            
            placeholder_text = "휴대폰 번호 입력" if ("카카오톡" in channel or "문자" in channel) else "수신자 주소/ID 입력"
            test_receiver = st.text_input("수신자 정보", placeholder=placeholder_text, key="test_receiver_input", label_visibility="visible")
            
            if st.button("테스트 발송", use_container_width=True, key="btn_test_send"):
                if not test_receiver:
                    st.warning("수신자 정보를 입력해주세요.")
                else:
                    try:
                        subject, body = parse_message_text(message)
                        send_id = str(uuid.uuid4())
                        status = send_campaign_message(channel, test_receiver, subject, body, image=img, send_id=send_id)

                        if status in [200, 202]:
                            st.markdown(
                                f"<div class='confirm-box'><span class='dot'></span>"
                                f"<span style='font-size:12px;color:#047857'>[{channel}] 테스트 메시지가 발송되었습니다.</span></div>",
                                unsafe_allow_html=True,
                            )
                            campaign_id = save_history(segment, message, 1, f"테스트 발송 ({channel})")
                            record_campaign_send(campaign_id, test_receiver, segment, channel, send_id)
                        else:
                            st.error(f"발송 실패: 응답 코드 {status}")
                    except Exception as e:
                        st.error(f"발송 실패: {e}")

        with st.container(border=True):
            send_freq = st.session_state.get("schedule_send_freq", "즉시 발송")
            is_repeating = send_freq in REPEATING_FREQS
            weekdays = st.session_state.get("schedule_weekdays", []) if send_freq == "특정 요일 반복" else []
            weekday_ready = send_freq != "특정 요일 반복" or len(weekdays) > 0

            button_label = f"반복 발송 등록 ({count}명 대상)" if is_repeating else f"전체 발송 ({count}명)"

            st.markdown("<p style='font-size:0.95rem; font-weight:700; margin-bottom:12px;'>전체 발송</p>", unsafe_allow_html=True)
            if is_repeating:
                interval_desc = {
                    "매일 발송": "매일",
                    "3일마다": "3일마다",
                    "일주일마다": "일주일마다",
                    "특정 요일 반복": "선택한 요일마다",
                }[send_freq]
                st.caption(f"{count}명 전체에게 [{channel}] 채널로 {interval_desc} 자동 반복 발송됩니다.")
            else:
                st.caption(f"{count}명 전체에게 [{channel}] 채널로 즉시/예약 발송됩니다.")

            if st.session_state["campaign_full_sent"]:
                done_label = "반복 발송이 등록되었습니다." if is_repeating else "발송 완료 — 캠페인이 성공적으로 등록되었습니다."
                st.markdown(
                    "<div class='confirm-box'><span class='dot'></span>"
                    f"<span style='font-size:12px;color:#047857'>{done_label}</span></div>",
                    unsafe_allow_html=True,
                )
            else:
                if not weekday_ready:
                    st.warning("반복할 요일을 선택해주세요. (2단계에서 지정할 수 있습니다)")

                if st.button(
                    button_label,
                    type="primary",
                    use_container_width=True,
                    disabled=not weekday_ready,
                    key="btn_full_send",
                ):
                    subject, body = parse_message_text(message)

                    if is_repeating:
                        first_run = datetime.combine(send_date, send_time)
                        register_recurring_campaign(
                            segment=segment,
                            channel=channel,
                            subject=subject,
                            body=body,
                            image_file=img,
                            freq=send_freq,
                            first_run=first_run,
                            send_time=send_time,
                            weekdays=weekdays,
                        )
                        st.session_state["campaign_full_sent"] = True
                        save_history(
                            segment, message, count,
                            f"반복 발송 등록 ({send_freq}, 첫 발송 {first_run.strftime('%Y-%m-%d %H:%M')} {channel})",
                        )
                        st.rerun()
                    else:
                        recipients = load_test_recipients()
                        if recipients.empty:
                            st.warning("data/test_recipients.csv 에 등록된 테스트 수신자가 없습니다.")
                        else:
                            is_immediate = send_freq == "즉시 발송"

                            if is_immediate:
                                target_dt = datetime.now(KST) if KST else datetime.now()
                                is_future = False
                                send_at_ts = None
                            else:
                                target_dt = datetime.combine(send_date, send_time)
                                if KST:
                                    target_dt = target_dt.replace(tzinfo=KST)
                                now = datetime.now(KST) if KST else datetime.now()
                                is_future = target_dt > now
                                send_at_ts = int(target_dt.timestamp()) if is_future else None

                            success_count, fail_count, succeeded_targets = 0, 0, []
                            succeeded_sends = []  # (target_info, send_id) - 즉시 발송 성공분만. campaign_history 행이 생긴 뒤에 기록한다 (FK)

                            for _, row in recipients.iterrows():
                                target_info = row.get("phone", row.get("email", ""))
                                try:
                                    send_id = str(uuid.uuid4())
                                    status = send_campaign_message(
                                        channel, target_info, subject, body, image=img,
                                        send_at=send_at_ts, send_id=send_id,
                                    )
                                    if status in [200, 202]:
                                        success_count += 1
                                        succeeded_targets.append(target_info)
                                        # 예약 발송은 나중에 실제로 보내지므로, 아직 발송되지
                                        # 않은 시점에 delivered=True로 기록하면 안 된다 - 즉시 발송일 때만 기록한다.
                                        if not is_future:
                                            succeeded_sends.append((target_info, send_id))
                                    else:
                                        fail_count += 1
                                except Exception as e:
                                    fail_count += 1
                                    st.error(f"{target_info} 발송 실패: {e}")

                            if success_count:
                                st.session_state["campaign_full_sent"] = True
                                if is_future:
                                    save_history(
                                        segment, message, success_count,
                                        f"예약 등록 완료 ({target_dt.strftime('%Y-%m-%d %H:%M')} {channel} 자동 발송 예정)",
                                    )
                                    save_scheduled_emails(segment, subject, succeeded_targets, target_dt)
                                else:
                                    campaign_id = save_history(
                                        segment, message, success_count,
                                        f"전체 발송 완료 ({channel} - {success_count + fail_count}명 중 {success_count}명 성공)",
                                    )
                                    # campaign_history 행이 위 save_history()로 먼저 생겨야
                                    # campaign_sends.campaign_id 외래키가 안 깨진다.
                                    for target_info, send_id in succeeded_sends:
                                        record_campaign_send(campaign_id, target_info, segment, channel, send_id)
                                st.rerun()
                            else:
                                st.error("발송에 실패했어요. 설정을 확인해주세요.")

    # --- Step 3 복구된 뒤로가기 버튼 ---
    st.markdown("<div style='margin-top: 16px;'></div>", unsafe_allow_html=True)
    bcol1, _ = st.columns([1, 1])
    with bcol1:
        if st.button("이전 단계 (발송 방식 설정)", use_container_width=True, key="btn_step3_prev"):
            st.session_state["campaign_full_sent"] = False
            st.session_state["campaign_step"] = 2
            st.rerun()


def render_campaign_builder():
    inject_custom_css()
    _init_campaign_state()
    _step_indicator(st.session_state["campaign_step"])

    if st.session_state["campaign_step"] == 1:
        _render_step1()
    elif st.session_state["campaign_step"] == 2:
        _render_step2()
    else:
        _render_step3()