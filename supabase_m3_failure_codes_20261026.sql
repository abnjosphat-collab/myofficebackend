-- M3 failure coding (owner-run, reviewed 26 Oct 2026). NOT RUN YET.
--
-- WHAT THIS DOES (verified read-only against live on 26 Oct 2026: 176 work
-- orders, lookup_lists holding the 'location' list only):
--   1. Adds work_orders.corrective_action (nullable text): the Action of the
--      Problem / Cause / Action triple a breakdown is closed with. Problem is
--      the existing failure_mode column, Cause the existing cause_of_failure
--      column — see backend/app/routers/maintenance.py and WORK_ORDERS.md.
--   2. Seeds three short starter lists in lookup_lists: failure_problem,
--      failure_cause, failure_action (8 values each). STARTER LIST — replace
--      with the eMaint codes. Managers edit these in the app (Admin panel >
--      Shared lists); artisans pick from them (or type a new value, which is
--      remembered) when closing a breakdown.
--
-- SAFE TO RUN: one transaction; a backup of the three lists' pre-existing rows
-- first (expected: none — the lists are new); guards stop it without changing
-- anything unless the shape still matches the review; every insert skips a
-- value already on the list (case-insensitive), so re-running is harmless.
-- Writes here do not go through the app, so they are not in the audit log;
-- the backup table is the record. Run it in the Supabase SQL editor.
--
-- RUN ORDER: any time after approval; no dependency on the M0/M1 files. The M3
-- backend code works before AND after this file runs (it probes for the
-- column): until it runs, a breakdown close asks for the action in the app
-- but the server neither requires nor stores it.
--
-- ROLLBACK (same editor): delete the seeded rows, restore any pre-existing
-- ones from the backup, then drop the column:
--   delete from public.lookup_lists
--     where list_name in ('failure_problem', 'failure_cause', 'failure_action');
--   insert into public.lookup_lists (list_name, value)
--     select list_name, value from public.m3_backup_20261026_lookup_lists;
--   alter table public.work_orders drop column if exists corrective_action;

begin;

-- Guards: the lookup_lists table must exist, and no M3 list may already hold
-- values the seed would silently sit beside (re-run is still harmless — the
-- inserts below skip matches — but a non-empty list means someone already
-- replaced the starter list, and this file must not run blindly over that).
do $$
declare n int;
begin
  if to_regclass('public.lookup_lists') is null then
    raise exception 'M3 guard: public.lookup_lists is missing — nothing changed.';
  end if;
  select count(*) into n from public.lookup_lists
    where list_name in ('failure_problem', 'failure_cause', 'failure_action');
  if n > 0 then
    raise exception 'M3 guard: % M3 list rows already exist — review them before seeding.', n;
  end if;
end $$;

-- Backup: the three lists' pre-existing rows (expected: none). --------------
create table if not exists public.m3_backup_20261026_lookup_lists (
  id integer, list_name text, value text
);
insert into public.m3_backup_20261026_lookup_lists (id, list_name, value)
  select id, list_name, value from public.lookup_lists
  where list_name in ('failure_problem', 'failure_cause', 'failure_action');

-- 1. The Action column (nullable: old rows and non-breakdowns stay blank). --
alter table public.work_orders
  add column if not exists corrective_action text;

-- 2. The starter lists (each insert skips a case-insensitive match). --------
-- Problem: what failed. -----------------------------------------------------
insert into public.lookup_lists (list_name, value)
select 'failure_problem', v
from (values
  ('Bearing failure'), ('Seal / gasket failure'), ('Motor failure'),
  ('Belt / chain failure'), ('Electrical fault'), ('Lubrication failure'),
  ('Overheating'), ('Blockage / fouling')
) as s(v)
where not exists (
  select 1 from public.lookup_lists l
  where l.list_name = 'failure_problem' and lower(l.value) = lower(s.v)
);

-- Cause: why it failed. -----------------------------------------------------
insert into public.lookup_lists (list_name, value)
select 'failure_cause', v
from (values
  ('Wear & tear'), ('Lack of lubrication'), ('Overloading'),
  ('Operator error'), ('Poor installation / alignment'), ('Corrosion'),
  ('Foreign object damage'), ('Age / fatigue')
) as s(v)
where not exists (
  select 1 from public.lookup_lists l
  where l.list_name = 'failure_cause' and lower(l.value) = lower(s.v)
);

-- Action: what fixed it. ----------------------------------------------------
insert into public.lookup_lists (list_name, value)
select 'failure_action', v
from (values
  ('Replaced component'), ('Repaired in place'), ('Adjusted / realigned'),
  ('Cleaned / unblocked'), ('Lubricated'), ('Tightened fastenings'),
  ('Rewound / rewired'), ('Referred to specialist')
) as s(v)
where not exists (
  select 1 from public.lookup_lists l
  where l.list_name = 'failure_action' and lower(l.value) = lower(s.v)
);

-- Post-check: each list holds exactly its 8 starter values. -----------------
do $$
declare n int;
begin
  select count(*) into n from public.lookup_lists
    where list_name in ('failure_problem', 'failure_cause', 'failure_action');
  if n <> 24 then
    raise exception 'M3 post-check: expected 24 seeded rows, found % — nothing changed.', n;
  end if;
end $$;

commit;
