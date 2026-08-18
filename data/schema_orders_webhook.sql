-- 주문(매출) 데이터는 브라우저 JS에 노출되는 api_key로 받으면 안 된다 (아무나 가짜 주문을 만들어 보낼 수 있음).
-- 그래서 회사 서버(백엔드)만 아는 별도의 webhook_secret을 추가해서, 주문 수신 API는 이걸로만 인증한다.

alter table companies add column if not exists webhook_secret text unique;

update companies set webhook_secret = 'athlepa_wh_7d3f9a2c88e1b640' where company_id = 'athlepa';
update companies set webhook_secret = 'dacon_wh_2a91c47fe6b3d508' where company_id = 'dacon_retail';
