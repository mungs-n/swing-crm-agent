"""
ab_test 모듈 공용 상수
담당: 가연
"""

# dashboard/charts.py의 ACCENT(#7C3AED) 등과 동일한 팔레트를 씀 (앱 전체 톤 통일).
# dashboard/charts.py를 직접 import하면 모듈 간 결합도가 높아지니, 색상 값만 복제해서 씀.
ACCENT = "#7C3AED"
PALE_PURPLE = "#C4B5FD"
EMERALD = "#10B981"
AMBER = "#F59E0B"
ROSE = "#F43F5E"
SUB_TEXT = "#6B7280"
LINE = "#E7E9F3"

# data/campaign_sends.csv의 segment 컬럼 실제 값과 1:1로 맞춤
SEGMENT_OPTIONS = [
    "전체",
    "신규 탐색자",
    "충동 구매자",
    "할인 구매자",
    "브랜드 충성 고객",
    "이탈 위험 고객",
    "휴면 고객",
]

CHANNEL_META = {
    "email": {"label": "이메일", "color": "#5B5BF7", "click_trackable": True},
    "kakao": {"label": "카카오톡", "color": "#F59E0B", "click_trackable": False},
    "sms": {"label": "메시지", "color": "#F43F5E", "click_trackable": False},
    "webpush": {"label": "웹 푸시", "color": "#10B981", "click_trackable": True},
    "webpopup": {"label": "웹 팝업", "color": "#8B5CF6", "click_trackable": True},
}

SUCCESS_METRICS = {
    "open": "오픈율",
    "click": "클릭률",
    "conversion": "전환율 (구매 완료)",
}

STATUS_META = {
    "진행중": {"color": AMBER},
    "완료": {"color": EMERALD},
}
