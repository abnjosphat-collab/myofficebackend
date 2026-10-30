-- 2027 PPE budget settings (Batch 3, 30 Oct 2026).
--
-- What this does:
--   1. New table ppe_budgets (year int primary key, settings jsonb, updated_by,
--      updated_at). The calculation lives in the frontend (budgetCalc.ts); the
--      server stores the edited settings and serves the year's working-day
--      calendar from app/zim_holidays.py so the screen and the downloads agree.
--      Seed nothing: the defaults live in code.
--
-- Undo: the table holds no live rows yet (guard below proves it on first run).
--   To roll back before any budget is saved:
--     drop table public.ppe_budgets;
--     drop table if exists public.ppe_budgets_backup_2026_10_30;
--   After a budget is saved, rolling back loses those settings: read the row
--   first (select * from public.ppe_budgets;) and keep the JSON.
--
-- Re-runnable: every object uses IF NOT EXISTS / CREATE OR REPLACE.
begin;

-- Dry run: what will change (RAISE NOTICE output, no writes).
do $$
declare budget_count int;
begin
  select count(*) into budget_count from public.ppe_budgets;
  raise notice 'DRY RUN ppe_budgets migration: ppe_budgets rows=% (refuses to seed; defaults live in code)', budget_count;
exception when undefined_table then
  raise notice 'DRY RUN: ppe_budgets does not exist yet; will be created empty.';
end $$;

-- 1. Budget settings, one row per year.
create table if not exists public.ppe_budgets (
  year int primary key,
  settings jsonb not null,
  updated_by text,
  updated_at timestamptz not null default now()
);

-- Backup (empty today, but the file owns it from the start).
create table if not exists public.ppe_budgets_backup_2026_10_30 as
  select * from public.ppe_budgets where false;

-- Guard: seed nothing. Refuse if any row exists that this migration did not write
-- (budgets are saved through the API only, so a pre-existing row means someone
-- saved one by hand and the defaults may no longer apply).
do $$
declare bad int;
begin
  select count(*) into bad from public.ppe_budgets;
  if bad > 0 then
    raise exception 'GUARD: ppe_budgets already holds % row(s); not seeded by hand. Read them before re-running.', bad;
  end if;
end $$;

-- 2. RLS (service_role only, as with the other PPE tables).
alter table public.ppe_budgets enable row level security;

commit;
