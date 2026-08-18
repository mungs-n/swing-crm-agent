# A/B 테스트 (담당: 가연)

자동화 탭 안의 A/B 테스트 서브탭 관련 코드가 들어가는 폴더입니다.

- 대상 선택 화면(세그먼트별/전체)
- 그룹 A/B 설정, 컨트롤 그룹, 성공 지표 선택
- SendGrid 실시간 오픈/클릭 데이터를 반영한 결과 화면

데이터는 `data/campaign_sends.csv`의 opened_at/clicked_at/converted_order_id 컬럼을 활용하면 됩니다.
