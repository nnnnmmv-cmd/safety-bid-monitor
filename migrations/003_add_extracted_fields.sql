-- 003: bids 테이블에 LLM 추출 7개 필드를 JSON 한 컬럼으로 저장
-- Supabase 콘솔 → SQL Editor → 붙여넣기 → Run

alter table public.bids
    add column if not exists extracted_fields jsonb default null;

-- 추후 검색·필터 빠르게 하려면 인덱스 (선택)
create index if not exists idx_bids_has_extracted on public.bids ((extracted_fields is not null));
