-- 입찰 게시판 수집 헬스 로그 (cafe_collection_health와 같은 역할)
-- Supabase 대시보드 → SQL Editor에 붙여넣고 Run 한 번.
-- 크롤러는 service_role 키로 접속하므로 RLS를 우회한다. 정책은 허브 화면(읽기)용.

create table if not exists public.bid_collection_health (
  id            bigint generated always as identity primary key,
  site_name     text        not null,
  run_at        timestamptz not null default now(),
  reason        text        not null,          -- 'ok' 또는 실패 사유
  posts_saved   integer     not null default 0, -- 이번에 새로 저장한 글 수
  posts_fetched integer     not null default 0  -- 목록에서 읽어낸 글 수(0이면 게시판이 안 읽힌 것)
);

-- 주 조회는 "게시판별 마지막 방문" — 그 순서로 인덱스
create index if not exists bid_collection_health_site_run_idx
  on public.bid_collection_health (site_name, run_at desc);

alter table public.bid_collection_health enable row level security;

-- 읽기 정책. 이게 없으면 anon/authenticated 조회가 조용히 0행이 된다(cron_runs와 같은 함정).
-- 허브 화면에서 안 읽을 거면 이 블록은 지워도 된다.
drop policy if exists bid_collection_health_read on public.bid_collection_health;
create policy bid_collection_health_read
  on public.bid_collection_health for select
  to authenticated
  using (true);
