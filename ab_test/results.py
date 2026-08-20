"""
A/B 테스트 결과 렌더링
담당: 가연
"""

import pandas as pd
import streamlit as st

from ab_test.constants import ACCENT, EMERALD, ROSE, STATUS_META, SUCCESS_METRICS
from ab_test.data import METRIC_COUNT_FIELD, cvr, load_ab_tests, refresh_ab_test_stats, test_summary_counts, two_proportion_test


def render_summary_header():
    """A/B 테스트 서브탭 최상단 타이틀 + 설명 + 진행중/완료/유의미 KPI 요약."""
    st.markdown("##### A/B 테스트")
    st.caption("A/B 테스트를 통해 어떤 메시지와 전략이 고객 전환에 더 효과적인지 비교해보세요.")

    counts = test_summary_counts()
    kpi_items = [("진행 중인 테스트", counts["running"]), ("완료된 테스트", counts["done"]), ("통계적으로 유의미", counts["significant"])]
    cols = st.columns(len(kpi_items))
    for col, (label, value) in zip(cols, kpi_items):
        with col:
            st.markdown(
                f"<div style='background:#F5F5F8;border:1px solid {STATUS_META.get('진행중', {}).get('color', '#E7E9F3')}22;"
                f"border-radius:10px;padding:12px 16px;text-align:center'>"
                f"<div style='font-size:1.3rem;font-weight:700'>{value}</div>"
                f"<div style='font-size:11px;color:#6B7280;margin-top:2px'>{label}</div></div>",
                unsafe_allow_html=True,
            )
    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)


def _status_badge(status: str) -> str:
    meta = STATUS_META.get(status, {"color": "#6B7280"})
    return (
        f"<span style='font-size:11.5px;font-weight:600;color:{meta['color']};"
        f"background:{meta['color']}14;padding:3px 9px;border-radius:20px'>{status}</span>"
    )


def _render_group_table(test_df: pd.DataFrame, success_metric: str = "conversion"):
    count_field = METRIC_COUNT_FIELD.get(success_metric, "conversions")
    metric_label = SUCCESS_METRICS.get(success_metric, SUCCESS_METRICS["conversion"])

    control = test_df[test_df["is_control"]]
    baseline = control.iloc[0] if not control.empty else test_df.iloc[0]

    rows_html = []
    for _, g in test_df.iterrows():
        rate = cvr(g["users"], g[count_field])
        if g["group_id"] == baseline["group_id"]:
            uplift, ci, p_value = None, None, None
        else:
            uplift, ci, p_value = two_proportion_test(
                int(baseline["users"]), int(baseline[count_field]), int(g["users"]), int(g[count_field])
            )

        tag = ""
        if g["is_control"]:
            tag = "<span style='font-size:10px;color:#6B7280;background:#F1F2F6;padding:1px 6px;border-radius:20px;margin-left:6px'>컨트롤</span>"
        if g["group_id"] == test_df.attrs.get("winner_group_id"):
            tag += (
                f"<span style='font-size:10px;font-weight:700;color:#fff;background:{ACCENT};"
                f"padding:1px 7px;border-radius:20px;margin-left:6px'>WINNER</span>"
            )

        if uplift is None:
            uplift_html = "<span style='color:#C7CAD6'>기준 그룹</span>"
        else:
            color = EMERALD if uplift > 0 else ROSE
            uplift_html = f"<span style='color:{color};font-weight:600'>{uplift:+.1f}%</span>"

        ci_html = f"{ci[0]:+.1f}%p ~ {ci[1]:+.1f}%p" if ci else "-"
        p_html = f"{p_value:.3f}" if p_value is not None else "-"
        if p_value is None:
            sig_html = "<span style='color:#C7CAD6;font-size:11.5px'>-</span>"
        elif p_value < 0.05:
            sig_html = f"<span style='color:{EMERALD};font-weight:700;font-size:11px;background:{EMERALD}14;padding:2px 8px;border-radius:20px'>유의미</span>"
        else:
            sig_html = "<span style='color:#6B7280;font-weight:700;font-size:11px;background:#F1F2F6;padding:2px 8px;border-radius:20px'>유의미하지 않음</span>"

        rows_html.append(
            "<tr style='border-bottom:1px solid #E7E9F3'>"
            f"<td style='padding:10px;font-weight:700'>{g['group_label']}{tag}</td>"
            f"<td style='padding:10px'>{int(g['users']):,}</td>"
            f"<td style='padding:10px'>{int(g[count_field]):,}</td>"
            f"<td style='padding:10px;font-weight:700'>{rate:.1f}%</td>"
            f"<td style='padding:10px'>{uplift_html}</td>"
            f"<td style='padding:10px;color:#6B7280'>{ci_html}</td>"
            f"<td style='padding:10px;color:#6B7280'>{p_html}</td>"
            f"<td style='padding:10px'>{sig_html}</td>"
            "</tr>"
        )

    headers = ["그룹", "총 유저 수", f"{metric_label} 이벤트 수", metric_label, "기준 대비 개선율", "차이 95% CI(%p)", "p-value", "유의성"]
    header_html = "".join(f"<th style='text-align:left;padding:8px 10px;color:#6B7280;font-size:11.5px'>{h}</th>" for h in headers)
    table_html = (
        "<table style='width:100%;border-collapse:collapse;font-size:13px'>"
        f"<thead><tr style='border-bottom:1px solid #E7E9F3'>{header_html}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption("* p-value 0.05 미만이면 통계적으로 유의미해요. 표본이 적은 그룹은 유의성 검정 없이 개선율만 참고용으로 표시돼요.")


def render_results():
    """생성된 A/B 테스트들을 테스트 단위로 카드에 렌더링. automation/page.py의
    'A/B 테스트' 서브탭에서 render_wizard() 아래에 이어서 호출."""
    refresh_ab_test_stats()
    ab_df = load_ab_tests()
    if ab_df.empty:
        st.info("아직 생성된 A/B 테스트가 없어요. 위에서 새 테스트를 만들어보세요.")
        return

    for test_id, test_df in ab_df.groupby("test_id"):
        test_name = test_df["test_name"].iloc[0]
        status = test_df["status"].iloc[0]
        winner_group_id = test_df["winner_group_id"].iloc[0] if "winner_group_id" in test_df else ""
        test_df = test_df.copy()
        test_df.attrs["winner_group_id"] = winner_group_id

        with st.container(border=True, key=f"ab-test-{test_id}"):
            col_title, col_status, col_action = st.columns([4, 1, 1.4])
            with col_title:
                st.markdown(f"<span style='font-size:15px;font-weight:700'>{test_name}</span>", unsafe_allow_html=True)
            with col_status:
                st.markdown(_status_badge(status), unsafe_allow_html=True)
            with col_action:
                if status == "진행중":
                    non_control = test_df[~test_df["is_control"]]
                    winner_label = st.selectbox(
                        "Winner", non_control["group_label"].tolist(),
                        key=f"winner-select-{test_id}", label_visibility="collapsed",
                    )
                    if st.button("테스트 종료", key=f"end-test-{test_id}"):
                        _end_test(test_id, winner_label, test_df)
                        st.rerun()

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            _render_group_table(test_df, test_df["success_metric"].iloc[0])


def _end_test(test_id: str, winner_label: str, test_df: pd.DataFrame):
    """테스트 종료 처리: status를 '완료'로 바꾸고 winner_group_id를 기록해서 csv에 반영"""
    winner_row = test_df[test_df["group_label"] == winner_label]
    winner_group_id = winner_row["group_id"].iloc[0] if not winner_row.empty else ""

    full_df = load_ab_tests()
    mask = full_df["test_id"] == test_id
    # winner_group_id/ended_at은 처음엔 전부 빈 문자열("")이라 CSV 왕복 후 NaN/NaT로
    # 읽혀서, 문자열을 그대로 대입하면 pandas가 dtype 불일치로 TypeError를 던진다.
    # object dtype으로 캐스팅해서 문자열을 담을 수 있게 만든 뒤 대입해야 한다.
    full_df["winner_group_id"] = full_df["winner_group_id"].astype(object)
    full_df["ended_at"] = full_df["ended_at"].astype(object)

    full_df.loc[mask, "status"] = "완료"
    full_df.loc[mask, "winner_group_id"] = winner_group_id
    full_df.loc[mask, "ended_at"] = pd.Timestamp.now().isoformat()
    full_df.to_csv("data/ab_tests.csv", index=False)
    load_ab_tests.clear()
