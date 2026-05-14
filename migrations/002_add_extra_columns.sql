-- 002: sites 테이블에 영업 메타데이터 4개 컬럼 추가
-- Supabase 콘솔 → SQL Editor → 붙여넣기 → Run

alter table public.sites
    add column if not exists above_100m_winner_method  text default '',
    add column if not exists bid_submission_method     text default '',
    add column if not exists performance_proof         text default '',
    add column if not exists work_overlap_doc          text default '';
