-- ATHLEPA CRM Supabase(Postgres) 스키마
-- 여러 데이터셋(ATHLEPA 자체 가상 데이터, 데이콘 실제 공개 데이터 등)을
-- 같은 테이블에 나란히 담을 수 있도록 모든 핵심 테이블에 dataset_source를 둔다.
-- 화면에서 데이터셋을 전환하면 WHERE dataset_source = '...' 조건만 바뀌는 구조.

create table if not exists users (
    id              bigserial primary key,
    dataset_source  text not null,              -- 'athlepa' / 'dacon' 등
    user_id         text not null,              -- 원본 데이터의 고객 ID
    name            text,                       -- 데이콘 등 실데이터는 없을 수 있음
    persona_type    text,                       -- 페르소나 라벨. 데이콘 데이터는 null
    age             int,
    gender          text,
    region          text,                       -- 데이콘의 고객지역 등. ATHLEPA는 null
    acquisition_channel text,
    signup_date     date,
    unique (dataset_source, user_id)
);

create table if not exists orders (
    id              bigserial primary key,
    dataset_source  text not null,
    order_id        text not null,
    user_id         text not null,
    order_date      timestamp not null,
    total_amount    int not null,
    discount_amount int default 0,
    coupon_used     boolean,
    category        text,
    unique (dataset_source, order_id),
    foreign key (dataset_source, user_id) references users (dataset_source, user_id)
);

create table if not exists events (
    id              bigserial primary key,
    dataset_source  text not null,
    user_id         text not null,
    session_id      text,
    "timestamp"     timestamp not null,
    event_type      text not null,   -- page_view / product_view / add_to_cart / purchase / review_write
    product_id      text,
    category        text,
    price           int,
    foreign key (dataset_source, user_id) references users (dataset_source, user_id)
);

-- 캠페인/발송 관련은 우리가 실제로 운영하는 캠페인이라 dataset_source를 두지 않고
-- 항상 'athlepa' 고정으로 취급한다 (데이콘 데이터로 캠페인을 발송하는 게 아니므로).

create table if not exists campaign_history (
    id              bigserial primary key,
    campaign_id     text unique not null,
    sent_at         timestamp not null,         -- 기존 "발송일시"
    segment         text not null,
    target_count    int not null,               -- 기존 "대상 인원"
    message_summary text,                       -- 기존 "메시지 요약"
    status          text,                       -- 기존 "상태"
    approval_mode   text default '자동실행'
);

create table if not exists campaign_sends (
    id                  bigserial primary key,
    send_id             text unique not null,
    campaign_id         text not null references campaign_history (campaign_id),
    user_id             text not null,          -- ATHLEPA 고정이므로 users(dataset_source='athlepa')를 참조
    segment             text,
    channel             text not null,          -- email / kakao / sms / webpush / webpopup
    sent_at             timestamp not null,
    delivered           boolean not null,
    opened_at           timestamp,              -- email만 신뢰도 있게 수집
    clicked_at          timestamp,              -- email/webpush/webpopup만 수집
    converted_order_id  text,
    conversion_type     text,                   -- direct / attributed
    revenue             int
);

-- 자주 쓰는 조회 패턴에 대한 인덱스
create index if not exists idx_users_source on users (dataset_source);
create index if not exists idx_orders_source_date on orders (dataset_source, order_date);
create index if not exists idx_events_source_ts on events (dataset_source, "timestamp");
create index if not exists idx_sends_campaign on campaign_sends (campaign_id);
create index if not exists idx_sends_sent_at on campaign_sends (sent_at);
