-- ============================================================
-- Work Orders — classification & analysis columns
-- Run in Supabase SQL Editor (Dashboard → SQL Editor → New query → Run)
-- ============================================================
--
-- Context: the maintenance page previously stored these six fields ONLY in the
-- browser's localStorage, because the backend had no columns for them. That meant
-- classification / failure mode / discipline / trade / spares-used were lost when a
-- user switched device or cleared storage. This migration adds the columns so the
-- fields persist server-side. Safe to run more than once (all IF NOT EXISTS).

alter table public.work_orders add column if not exists classification        text;
alter table public.work_orders add column if not exists classification_custom text;
alter table public.work_orders add column if not exists failure_mode          text;
alter table public.work_orders add column if not exists discipline            text;
alter table public.work_orders add column if not exists trade                 text;

-- spares_used is a list of { id, name, quantity, unit_cost } objects → JSONB.
alter table public.work_orders add column if not exists spares_used jsonb not null default '[]'::jsonb;

-- Optional: indexes to speed up the analytics filters that group by these fields.
create index if not exists idx_work_orders_classification on public.work_orders (classification);
create index if not exists idx_work_orders_failure_mode   on public.work_orders (failure_mode);
create index if not exists idx_work_orders_discipline      on public.work_orders (discipline);
