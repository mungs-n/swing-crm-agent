"""
캠페인 퍼포먼스 대시보드 공용 상수
담당: 가연

dashboard/charts.py를 직접 import하면 모듈 간 결합도가 높아지니, ab_test/constants.py와
마찬가지로 색상 값만 복제해서 쓴다.
"""

ACCENT = "#5B5BF7"
ACCENT_SOFT = "#EEF0FF"
INK = "#1C2130"
SUB_TEXT = "#6B7280"
LINE = "#E7E9F3"
BG = "#F7F8FC"
CARD = "#FFFFFF"
EMERALD = "#10B981"
AMBER = "#F59E0B"
ROSE = "#F43F5E"

CHANNEL_META = {
    "email": {"label": "이메일", "color": "#5B5BF7"},
    "kakao": {"label": "카카오톡", "color": "#F59E0B"},
    "sms": {"label": "메시지", "color": "#F43F5E"},
    "webpush": {"label": "웹 푸시", "color": "#10B981"},
    "webpopup": {"label": "웹 팝업", "color": "#8B5CF6"},
}

STATUS_META = {
    "scheduled": {"label": "예약됨", "color": AMBER},
    "completed": {"label": "완료됨", "color": SUB_TEXT},
}

IMPACT_BASIS_META = {
    "ab": {"label": "AB 테스트 기반", "color": EMERALD},
    "prepost": {"label": "전후 비교(참고용)", "color": AMBER},
}

METRIC_DEFS = [
    {"key": "sent", "label": "발송", "color": "#94A3B8", "axis": "left"},
    {"key": "clicks", "label": "클릭", "color": AMBER, "axis": "left"},
    {"key": "conversions", "label": "전환", "color": EMERALD, "axis": "left"},
    {"key": "revenue", "label": "전환매출", "color": ACCENT, "axis": "right"},
]
