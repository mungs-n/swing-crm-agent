-- 실시간 트래킹 서버가 회사를 구분할 때 쓰는 api_key.
-- 로그인 비밀번호와는 별개로, 웹사이트에 심는 트래킹 스니펫에 노출되는 키라서 companies에 따로 둔다.
--
-- 주의: 실제 키 값은 여기에 커밋하지 않는다 (GitGuardian이 시크릿으로 탐지해서 노출됨 —
-- 실제로 겪은 사고). 컬럼만 추가하고, 값은 Supabase SQL Editor에서 직접 UPDATE 하거나
-- data/seed_auth.py 같은 시딩 스크립트로 그때그때 생성해서 넣는다.

alter table companies add column if not exists api_key text unique;
