-- 실시간 트래킹은 아직 users 테이블에 없는 "익명 방문자"의 이벤트도 받아야 하는데,
-- events.user_id -> users(dataset_source, user_id) 외래키 제약 때문에 막혀 있었다.
-- 실제 GA4/Mixpanel류 트래킹도 익명 방문자를 먼저 받고 나중에 식별하는 구조라
-- 이 제약은 events 테이블에서 제거한다 (users 테이블 자체의 unique 제약은 그대로 둔다).

do $$
declare
  con_name text;
begin
  select conname into con_name
  from pg_constraint
  where conrelid = 'events'::regclass
    and contype = 'f';
  if con_name is not null then
    execute format('alter table events drop constraint %I', con_name);
  end if;
end $$;
