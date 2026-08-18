-- 실시간 트래킹 서버가 회사를 구분할 때 쓰는 api_key.
-- 로그인 비밀번호와는 별개로, 웹사이트에 심는 트래킹 스니펫에 노출되는 키라서 companies에 따로 둔다.

alter table companies add column if not exists api_key text unique;

update companies set api_key = 'athlepa_demo_9f3a7c21e6b84d0f' where company_id = 'athlepa';
update companies set api_key = 'dacon_demo_4b2f88a1c9e0573a' where company_id = 'dacon_retail';
