"""
A/B 테스트 생성 화면
담당: 가연

한 화면에 블록(카드) 4개를 세로로 배치: 대상 선택 → 그룹 설정 → 그룹별 메시지 →
성공 지표. 예전엔 페이지네이션(이전/다음)이 있는 3단계 위저드였는데, 카드형
레이아웃으로 바뀌면서 전부 한 화면에서 스크롤로 보이게 바꿨다. 대상 선택이
맨 위(가장 먼저)인 순서는 그대로 유지.
"""

import uuid
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st

from ab_test.constants import ACCENT, CHANNEL_META, SEGMENT_OPTIONS, SUCCESS_METRICS
from ab_test.data import load_campaign_sends, save_ab_test
from automation.email_sender import KST, load_test_recipients, record_campaign_send, save_history, send_email


def _init_state():
    st.session_state.setdefault("ab_target_segment", "전체")
    st.session_state.setdefault("ab_channel", "email")
    st.session_state.setdefault("ab_groups", [
        {"group_id": "a", "label": "A", "ratio": 50},
        {"group_id": "b", "label": "B", "ratio": 50},
    ])
    st.session_state.setdefault("ab_include_control", True)
    st.session_state.setdefault("ab_control_ratio", 20)
    st.session_state.setdefault("ab_success_metric", "open")
    st.session_state.setdefault("ab_messages", {})  # group_id -> {"title", "text", "image_name", "image_bytes"}
    st.session_state.setdefault("ab_send_date", datetime.now().date())
    st.session_state.setdefault("ab_send_time", time(9, 0))


def _reset_state():
    for key in [
        "ab_target_segment", "ab_channel", "ab_groups", "ab_include_control", "ab_control_ratio",
        "ab_success_metric", "ab_messages", "ab_send_date", "ab_send_time",
    ]:
        st.session_state.pop(key, None)


def _format_kr_datetime(d, t) -> str:
    """2026년 1월 25일 오후 4:20 형태로 표시 (미리보기 배지용)"""
    hour_12 = t.hour % 12 or 12
    ampm = "오전" if t.hour < 12 else "오후"
    return f"{d.year}년 {d.month}월 {d.day}일 {ampm} {hour_12}:{t.minute:02d}"


def _apportion(n: int, ratios: list[int]) -> list[int]:
    """정수 n명을 ratios(합 100) 비율대로 나눠 정수 리스트로 돌려준다. 그냥 각각
    round(n*ratio/100)로 나누면, 테스트 수신자가 적을 때(예: 1~2명) 반올림 때문에
    모든 그룹이 0명이 되어 아무도 배정 못 받는 경우가 생긴다(사람이 통째로 증발).
    최대잔여법(largest remainder method)을 써서, 몫의 합이 항상 n이 되도록 하고
    비율이 큰 그룹부터 남는 인원을 확실히 배정한다."""
    raw = [n * r / 100 for r in ratios]
    counts = [int(x) for x in raw]
    remainder = n - sum(counts)
    order = sorted(range(len(ratios)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[:remainder]:
        counts[i] += 1
    return counts


def _target_size(segment: str) -> int:
    sends = load_campaign_sends()
    if segment == "전체":
        return sends["user_id"].nunique()
    return sends.loc[sends["segment"] == segment, "user_id"].nunique()


def _block_target():
    with st.container(border=True):
        st.markdown("**대상 선택**")
        col1, col2 = st.columns([2, 1])
        with col1:
            st.session_state["ab_target_segment"] = st.selectbox(
                "대상 세그먼트", SEGMENT_OPTIONS,
                index=SEGMENT_OPTIONS.index(st.session_state["ab_target_segment"]),
                help="세그먼트별로 나눠 테스트하거나, '전체'를 선택해 전체 고객 대상으로 테스트할 수 있어요.",
            )
            st.session_state["ab_channel"] = st.selectbox(
                "발송 채널", list(CHANNEL_META.keys()),
                index=list(CHANNEL_META.keys()).index(st.session_state["ab_channel"]),
                format_func=lambda c: CHANNEL_META[c]["label"],
            )
        with col2:
            size = _target_size(st.session_state["ab_target_segment"])
            st.metric("예상 대상 인원", f"{size:,}명")
            if not CHANNEL_META[st.session_state["ab_channel"]]["click_trackable"]:
                st.caption(":material/info: 이 채널은 클릭 추적이 안 돼요. 성공 지표에서 클릭률은 선택하지 마세요.")


def _add_group():
    groups = st.session_state["ab_groups"]
    next_letter = chr(65 + len(groups))
    st.session_state["ab_groups"].append({"group_id": next_letter.lower(), "label": next_letter, "ratio": 0})


def _block_groups():
    """그룹 비율은 항상 합이 100%가 되도록 자동 배분한다. 컨트롤(홀드아웃) 비율은
    보통 팀이 의도적으로 작게 고정해두는 값이라 직접 슬라이더로 조절하게 두고,
    A/B 그룹들은 '컨트롤을 뺀 나머지' 안에서 순서대로 소비하는 방식으로 자동 배분한다
    (마지막 그룹은 슬라이더 없이 남은 비율을 그대로 가져감 - 그래서 항상 100%)."""
    with st.container(border=True):
        col_title, col_add = st.columns([4, 1.2])
        with col_title:
            st.markdown("**A/B 테스트 그룹 설정**")
        with col_add:
            if len(st.session_state["ab_groups"]) < 5:
                st.button(":material/add: 그룹 추가", key="grp-add", on_click=_add_group, use_container_width=True)

        groups = st.session_state["ab_groups"]
        size = _target_size(st.session_state["ab_target_segment"])

        # 컨트롤(홀드아웃)은 박스/뱃지로 안 빼고, 슬라이더 색만 회색으로 바꿔서
        # "발송 없는 그룹"이라는 걸 A/B 그룹(보라색)과 구분한다. Streamlit 슬라이더는
        # 색을 직접 지정하는 옵션이 없어서, 이 슬라이더만 감싼 컨테이너에 grayscale
        # 필터를 걸어 우회한다 (값 위치에 따른 채움 너비 로직은 그대로 두고 색만 뺌).
        st.markdown(
            "<style>div[class*='st-key-grp-control-slider'] [data-testid='stSlider']"
            " { filter: grayscale(1); }</style>",
            unsafe_allow_html=True,
        )
        st.session_state["ab_include_control"] = st.toggle(
            "컨트롤 그룹 포함 (발송 없음 — 기준선)", value=st.session_state["ab_include_control"], key="grp-include-control",
        )
        control_ratio = 0
        if st.session_state["ab_include_control"]:
            cols = st.columns([2, 4, 1])
            with cols[0]:
                st.markdown("<div style='padding-top:8px;color:#6B7280'>컨트롤</div>", unsafe_allow_html=True)
            with cols[1]:
                with st.container(key="grp-control-slider"):
                    st.session_state["ab_control_ratio"] = st.slider(
                        "컨트롤 비율(%)", 0, 100, st.session_state["ab_control_ratio"], key="grp-ctrl-ratio", label_visibility="collapsed",
                    )
            with cols[2]:
                st.markdown(
                    f"<div style='padding-top:8px;font-weight:700;color:#6B7280'>{st.session_state['ab_control_ratio']}%</div>",
                    unsafe_allow_html=True,
                )
            control_ratio = st.session_state["ab_control_ratio"]

        remaining = max(0, 100 - control_ratio)
        for i, g in enumerate(groups):
            is_last = i == len(groups) - 1
            cols = st.columns([2, 4, 1])
            with cols[0]:
                g["label"] = st.text_input("그룹명", value=g["label"], key=f"grp-label-{i}", label_visibility="collapsed")
            with cols[1]:
                if is_last:
                    g["ratio"] = remaining
                    st.markdown("<div style='padding-top:8px;color:#9AA0AE;font-size:12.5px'>나머지 자동 배정</div>", unsafe_allow_html=True)
                else:
                    g["ratio"] = st.slider(
                        "비율(%)", 0, remaining, min(g["ratio"], remaining), key=f"grp-ratio-{i}", label_visibility="collapsed",
                    )
                    remaining -= g["ratio"]
            with cols[2]:
                st.markdown(f"<div style='padding-top:8px;font-weight:700;color:{ACCENT}'>{g['ratio']}%</div>", unsafe_allow_html=True)

        for g in groups:
            st.caption(f"{g['label']} · 약 {int(size * g['ratio'] / 100):,}명")


def _message_editor(group_id: str, group_label: str):
    """그룹별 메시지 제목/본문 + 이미지 첨부(드롭존) 에디터"""
    st.session_state["ab_messages"].setdefault(group_id, {"title": "", "text": "", "image_name": None, "image_bytes": None})
    msg_state = st.session_state["ab_messages"][group_id]

    st.markdown(f"**{group_label} 메시지**")
    msg_state["title"] = st.text_input(
        "메시지 제목", value=msg_state.get("title", ""), key=f"msg-title-{group_id}", placeholder="메시지 제목 입력",
    )
    msg_state["text"] = st.text_area(
        "메시지 본문", value=msg_state["text"], key=f"msg-text-{group_id}", height=100, placeholder="메시지 내용 입력",
    )

    if msg_state.get("image_name"):
        col_img, col_remove = st.columns([4, 1])
        with col_img:
            st.image(msg_state["image_bytes"], caption=msg_state["image_name"], width=160)
        with col_remove:
            if st.button(":material/close: 제거", key=f"msg-img-remove-{group_id}"):
                msg_state["image_name"] = None
                msg_state["image_bytes"] = None
                st.rerun()
    else:
        uploaded = st.file_uploader(
            "이미지 추가 (클릭 또는 드래그)", type=["png", "jpg", "jpeg"], key=f"msg-img-{group_id}",
            help="이메일/웹푸시 등에 들어갈 이미지를 드래그해서 놓거나 클릭해서 선택하세요.",
        )
        if uploaded is not None:
            msg_state["image_name"] = uploaded.name
            msg_state["image_bytes"] = uploaded.getvalue()
            st.rerun()


def _block_messages():
    with st.container(border=True):
        st.markdown("**그룹별 메시지**")

        col_date, col_time = st.columns(2)
        with col_date:
            st.session_state["ab_send_date"] = st.date_input("발송 날짜", value=st.session_state["ab_send_date"])
        with col_time:
            st.session_state["ab_send_time"] = st.time_input("발송 시각", value=st.session_state["ab_send_time"])

        send_label = _format_kr_datetime(st.session_state["ab_send_date"], st.session_state["ab_send_time"])
        st.markdown(
            f"<span style='display:inline-block;font-size:12px;font-weight:600;color:{ACCENT};"
            f"background:{ACCENT}14;padding:3px 10px;border-radius:20px'>{send_label} 발송 예정</span>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        groups = st.session_state["ab_groups"]
        tabs = st.tabs([g["label"] for g in groups]) if groups else []
        for tab, g in zip(tabs, groups):
            with tab:
                _message_editor(g["group_id"], g["label"])


def _block_success_metric():
    with st.container(border=True):
        st.markdown("**성공 지표**")
        st.session_state["ab_success_metric"] = st.radio(
            "성공 지표", list(SUCCESS_METRICS.keys()),
            format_func=lambda k: SUCCESS_METRICS[k],
            index=list(SUCCESS_METRICS.keys()).index(st.session_state["ab_success_metric"]),
            horizontal=True,
            label_visibility="collapsed",
        )


def render_wizard():
    """A/B 테스트 생성 화면 전체 렌더 - ab_test/page.py의 위저드 전용 화면에서 호출.
    목록 화면을 완전히 대체하는 화면이라, 블록(카드) 4개를 세로로 나열하고 페이지네이션은 없다."""
    _init_state()

    _block_target()
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    _block_groups()
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    _block_messages()
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    _block_success_metric()

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    col_cancel, col_submit, _ = st.columns([1, 1.4, 3])
    with col_cancel:
        if st.button("취소", key="wizard-cancel", use_container_width=True):
            _reset_state()
            st.session_state["ab_wizard_open"] = False
            st.rerun()
    with col_submit:
        if st.button("테스트 시작", key="wizard-submit", type="primary", use_container_width=True):
            _submit_test()


def _submit_test():
    """channel이 email이면 실제로 SendGrid 발송까지 한다 — email_sender.py에 이미 있는
    발송/기록 함수(send_email/save_history/record_campaign_send)를 캠페인 만들기 탭과
    똑같은 방식으로 재사용하는 거라 그 파일 자체는 안 건드린다. email 외 채널은 실제
    발송 함수가 없어서 기존처럼 그룹 행만 만들어두는 시뮬레이션으로 남겨둔다.
    그룹별 오픈/클릭/전환 수는 ab_test/data.py의 refresh_ab_test_stats()가 이후
    campaign_sends를 다시 집계해서 채워준다."""
    groups = [{**g, "is_control": False} for g in st.session_state["ab_groups"]]
    if st.session_state["ab_include_control"]:
        groups.append({
            "group_id": "ctrl", "label": "컨트롤 (미발송)", "is_control": True,
            "ratio": st.session_state["ab_control_ratio"],
        })

    if sum(g["ratio"] for g in groups) != 100:
        st.error("그룹 비율 합이 100%가 되도록 맞춰주세요.")
        return
    if st.session_state["ab_include_control"] and sum(1 for g in groups if g["is_control"]) != 1:
        st.error("컨트롤 그룹은 정확히 1개여야 해요.")
        return

    channel = st.session_state["ab_channel"]
    is_real_send = channel == "email"
    send_at_ts = None

    if is_real_send:
        for g in groups:
            if g["is_control"] or g["ratio"] <= 0:
                continue
            msg = st.session_state["ab_messages"].get(g["group_id"], {})
            if not msg.get("title", "").strip() or not msg.get("text", "").strip():
                st.error(f"'{g['label']}' 그룹의 메시지 제목/본문을 입력해주세요.")
                return

        recipients = load_test_recipients()
        if recipients.empty:
            st.error(
                "data/test_recipients.csv 에 등록된 테스트 수신자가 없어서 실제 발송을 할 수 없어요. "
                "name,email 컬럼으로 등록해주세요."
            )
            return

        target_dt = datetime.combine(st.session_state["ab_send_date"], st.session_state["ab_send_time"])
        if KST:
            target_dt = target_dt.replace(tzinfo=KST)
        now = datetime.now(KST) if KST else datetime.now()
        is_future = target_dt > now
        if is_future and target_dt > now + timedelta(hours=72):
            st.error("SendGrid는 최대 72시간 이내 예약만 지원해요. 발송 시각을 다시 확인해주세요.")
            return
        send_at_ts = int(target_dt.timestamp()) if is_future else None

    test_id = str(uuid.uuid4())[:8]
    segment = st.session_state["ab_target_segment"]
    test_row = {
        "test_id": test_id,
        "test_name": f"{segment} · {CHANNEL_META[channel]['label']} 테스트",
        "segment": segment,
        "channel": channel,
        "success_metric": st.session_state["ab_success_metric"],
        "status": "진행중",
        "created_at": pd.Timestamp.now().isoformat(),
        "ended_at": "",
        "winner_group_id": "",
    }

    if is_real_send:
        group_rows = []
        idx = 0
        n = len(recipients)
        counts = _apportion(n, [g["ratio"] for g in groups])
        for g, count in zip(groups, counts):
            group_recipients = recipients.iloc[idx: idx + count]
            idx += count

            campaign_id = ""
            if not g["is_control"] and not group_recipients.empty:
                msg = st.session_state["ab_messages"][g["group_id"]]
                subject, body = msg["title"].strip(), msg["text"].strip()
                campaign_id = f"{test_id}_{g['group_id']}"

                success_count = 0
                succeeded_sends = []  # (email, send_id) - campaign_history 행이 생긴 뒤에 기록 (FK)
                for _, row in group_recipients.iterrows():
                    send_id = str(uuid.uuid4())
                    try:
                        status = send_email(
                            row["email"], subject, body, send_at=send_at_ts, send_id=send_id,
                            image_bytes=msg.get("image_bytes"), image_name=msg.get("image_name"),
                        )
                        if status == 202:
                            success_count += 1
                            if send_at_ts is None:
                                succeeded_sends.append((row["email"], send_id))
                    except Exception as e:
                        st.error(f"{row['email']} 발송 실패: {e}")

                if success_count:
                    save_history(
                        segment, f"제목: {subject}\n\n본문: {body}", success_count,
                        f"AB테스트 발송 ({g['label']} 그룹)", campaign_id=campaign_id,
                    )
                    for email, send_id in succeeded_sends:
                        record_campaign_send(campaign_id, email, segment, channel, send_id)

            group_rows.append({
                "group_id": g["group_id"], "group_label": g["label"], "is_control": g["is_control"],
                "campaign_id": campaign_id, "users": len(group_recipients),
                "conversions": 0, "clicks": 0, "opens": 0,
            })
    else:
        size = _target_size(segment)
        group_rows = [
            {
                "group_id": g["group_id"], "group_label": g["label"], "is_control": g["is_control"],
                "campaign_id": "", "users": int(size * g["ratio"] / 100),
                "conversions": 0, "clicks": 0, "opens": 0,
            }
            for g in groups
        ]

    save_ab_test(test_row, group_rows)
    _reset_state()
    st.session_state["ab_wizard_open"] = False
    if is_real_send:
        sent_total = sum(r["users"] for r in group_rows if not r["is_control"])
        st.success(f"'{test_row['test_name']}' 테스트가 생성되고 실제로 {sent_total}명에게 발송됐어요.")
    else:
        st.success(
            f"'{test_row['test_name']}' 테스트가 생성됐어요. "
            f"({CHANNEL_META[channel]['label']} 채널은 아직 실제 발송 연동 전이라 인원수만 채워둔 시뮬레이션이에요.)"
        )
    st.rerun()
