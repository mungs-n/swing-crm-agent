-- 주문(매출) 데이터는 브라우저 JS에 노출되는 api_key로 받으면 안 된다 (아무나 가짜 주문을 만들어 보낼 수 있음).
-- 그래서 회사 서버(백엔드)만 아는 별도의 webhook_secret을 추가해서, 주문 수신 API는 이걸로만 인증한다.
--
-- 주의: 실제 키 값은 여기에 커밋하지 않는다 (GitGuardian이 시크릿으로 탐지해서 노출됨 —
-- 실제로 겪은 사고). 컬럼만 추가하고, 값은 Supabase SQL Editor에서 직접 UPDATE 하거나
-- data/seed_auth.py 같은 시딩 스크립트로 그때그때 생성해서 넣는다.

alter table companies add column if not exists webhook_secret text unique;
