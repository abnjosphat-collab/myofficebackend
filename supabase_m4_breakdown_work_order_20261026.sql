-- M4 one entry for breakdowns (owner-run, reviewed 26 Oct 2026). NOT RUN YET.
--
-- WHAT THIS DOES (verified read-only against live on 26 Oct 2026: 8
-- breakdowns, 25 breakdown-classified jobs — 23 of them test rows leaked by
-- an early M4 test run, listed in M4_REPORT.md for the owner to delete —
-- neither table carrying the link):
--   Adds breakdowns.work_order_id (nullable integer): the job raised for the
--   breakdown, with a foreign key to work_orders(id) ON DELETE SET NULL
--   (deleting a job unlinks its breakdown; the downtime record survives)
--   plus an index for the work-order-to-breakdown read. ONE source of truth:
--   the work order's breakdown is read back through this same column, so the
--   two directions can never disagree. No backfill: none of the 8 live
--   breakdowns (Dec 2025 – Jan 2026) matches any live breakdown job by
--   machine (checked 26 Oct 2026: the 2 real ones, WO-00010 and WO-00115,
--   plus 23 leaked test rows then present), so every row starts unlinked
--   and links from go-live. See backend/app/breakdown_links.py and the
--   "One entry for breakdowns (M4)" section in WORK_ORDERS.md.
--
-- SAFE TO RUN: one transaction; a whole-table backup first; guards stop it
-- without changing anything unless the shape still matches the review.
-- Writes here do not go through the app, so they are not in the audit log;
-- the backup table is the record. Run it in the Supabase SQL editor.
--
-- RUN ORDER: any time after approval; no dependency on the M0/M1/M3 files.
-- The M4 backend code works before AND after this file runs (it probes for
-- the column): until it runs, logging a breakdown still raises its job but
-- stores no link, and breakdown shells are skipped.
--
-- ROLLBACK (same editor): drop what this file added (the backup stays for
-- reference; drop it too once the owner is satisfied):
--   alter table public.breakdowns drop constraint if exists fk_breakdowns_work_order;
--   drop index if exists public.ix_breakdowns_work_order;
--   alter table public.breakdowns drop column if exists work_order_id;

begin;

-- Guards: both tables must exist, and the link must not already be there
-- (a second run, or a column someone added by hand, stops here). ---------
do $$
begin
  if to_regclass('public.breakdowns') is null then
    raise exception 'M4 guard: public.breakdowns is missing — nothing changed.';
  end if;
  if to_regclass('public.work_orders') is null then
    raise exception 'M4 guard: public.work_orders is missing — nothing changed.';
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'breakdowns'
      and column_name = 'work_order_id'
  ) then
    raise exception 'M4 guard: breakdowns.work_order_id already exists — nothing changed.';
  end if;
end $$;

-- Backup: the whole breakdowns table (8 rows on 26 Oct 2026). ----------------
create table public.m4_backup_20261026_breakdowns as
  select * from public.breakdowns;

-- 1. The link column (nullable: old rows start unlinked). --------------------
alter table public.breakdowns
  add column work_order_id integer;

-- 2. The foreign key (unlink on job delete, never block or cascade). ---------
alter table public.breakdowns
  add constraint fk_breakdowns_work_order
  foreign key (work_order_id) references public.work_orders (id)
  on delete set null;

-- 3. The index for the work-order-to-breakdown read. --------------------------
create index ix_breakdowns_work_order
  on public.breakdowns (work_order_id);

-- Post-check: the column, key and index are all in place; report links. ------
do $$
declare linked int;
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'breakdowns'
      and column_name = 'work_order_id'
  ) then
    raise exception 'M4 post-check: work_order_id missing — nothing changed.';
  end if;
  if to_regclass('public.ix_breakdowns_work_order') is null then
    raise exception 'M4 post-check: ix_breakdowns_work_order missing — nothing changed.';
  end if;
  select count(*) into linked from public.breakdowns where work_order_id is not null;
  raise notice 'M4 post-check: % linked breakdowns (expected 0 — links start at go-live).', linked;
end $$;

commit;
