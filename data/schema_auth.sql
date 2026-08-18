-- 로그인 기반 멀티테넌시를 위한 추가 테이블.
-- companies: 플랫폼을 이용하는 기업(테넌트) 목록. 기업마다 어느 dataset_source를 보는지 매핑.
-- platform_users: 그 기업 소속으로 로그인하는 계정. 비밀번호는 해시로만 저장.

create table if not exists companies (
    company_id      text primary key,
    company_name    text not null,
    dataset_source  text not null,   -- users/orders/events 테이블의 dataset_source와 매칭
    created_at      timestamp default now()
);

create table if not exists platform_users (
    id              bigserial primary key,
    email           text unique not null,
    password_hash   text not null,
    company_id      text not null references companies (company_id),
    created_at      timestamp default now()
);
