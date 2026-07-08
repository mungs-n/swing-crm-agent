<<<<<<< HEAD
# 🤖 swing-crm-agent

> 2026 SWING 해외 프로그램 in 실리콘밸리  
> CRM 분야에서의 AI 에이전트를 활용한 자동화

## 팀원 역할

| 이름 | 담당 파일 |
|------|-----------|
| 차트 담당 | `components/charts.py` |
| AI 담당 | `components/ai_insights.py` |
| 탭2 담당 A | `components/campaign_builder.py` |
| 탭2 담당 B | `components/email_sender.py` |

## 시작하기

### 1. 레포 클론
```bash
git clone https://github.com/mungs-n/swing-crm-agent.git
cd swing-crm-agent
```

### 2. 패키지 설치
```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정
```bash
cp .env.example .env
# .env 파일 열어서 API 키 입력
```

### 4. 데이터 생성
```bash
python data/generate_data.py
```

### 5. 앱 실행
```bash
streamlit run 🏠_Home.py
```

## 브랜치 규칙

```bash
git checkout -b feature/charts       # 차트 담당
git checkout -b feature/ai-insights  # AI 담당
git checkout -b feature/campaign     # 탭2 담당 A
git checkout -b feature/email        # 탭2 담당 B
```

작업 완료 후 main에 PR 올리기

## 기술 스택

- 웹 애플리케이션: Streamlit
- 시각화: Plotly
- AI: Claude API (Tool Use)
- 데이터 처리: pandas
- 이메일 발송: SendGrid
=======
# swing-crm-agent
>>>>>>> 6007f84d98fa14fc798a217c93e899cac4b508fb
