-- M1 maintenance links + timestamps (owner-run, reviewed 26 Oct 2026). NOT RUN YET.
--
-- WHAT THIS DOES (verified read-only against live on 26 Oct 2026: 176 work
-- orders, 17 schedules, 164 runs, 43 register items):
--   1. Adds link columns: work_orders.equipment_id / .schedule_id,
--      maintenance_schedules.equipment_id — all nullable, all with
--      ON DELETE SET NULL foreign keys (deleting a machine or a schedule
--      unlinks, never blocks or cascades).
--   2. Adds server-stamped time columns on work_orders: started_at,
--      completed_at, signed_off_at (all nullable timestamptz; the app stamps
--      them from here on — see backend/app/routers/maintenance.py).
--   3. Adds inheritance columns on schedules: classification, discipline,
--      trade (nullable text; generated jobs copy them, see schedules.py).
--   4. Backfills, by rule (no guessing):
--        schedules.equipment_id  — exact name match (trimmed both sides), or
--                                  the register id inside a compressor-routine:NN marker.
--        work_orders.schedule_id — from maintenance_schedule_runs (each work
--                                  order sits on at most one run; guarded).
--        work_orders.equipment_id — via its schedule's link first, else its
--                                  own marker, else exact name match.
--        work_orders.classification — 'planned_maintenance' on schedule-linked
--                                  rows that have none (schedules carry no
--                                  discipline/trade values yet, so there is
--                                  nothing to inherit for those).
--        started_at/completed_at — the 2 completed July rows, from artisan_date
--                                  + time_work_started/_finished (Harare wall
--                                  time); signed_off_at from foreman_date
--                                  (date precision only) where a foreman sign
--                                  exists. Nothing else is knowable: the old
--                                  rows never recorded when work started.
--   5. Reports what could NOT be linked (exact names with no register match —
--      on 26 Oct that was 'hbub' only, plus the compressor markers which link
--      by id, not name).
--
-- SAFE TO RUN: one transaction; whole-table backups first; guards stop it
-- without changing anything unless the shape still matches the review (marker
-- ids all on the register, runs map 1:1 onto work orders, M1 columns absent).
-- Writes here do not go through the app, so they are not in the audit log;
-- the backup tables are the record. Run it in the Supabase SQL editor.
--
-- RUN ORDER: after M0's cleanup if possible (so the backfill only touches real
-- rows), but either order works — M0's guards and deletes are unaffected by
-- these columns, and the backfill rules re-derive the same links on renamed
-- rows via schedule_id. The M1 backend code works before AND after this file
-- runs (it probes for the columns); until it runs, new rows simply stay
-- unlinked and unstamped.
--
-- ROLLBACK (same editor, before dropping backups): the new columns hold only
-- backfilled/app-written values, so dropping them loses nothing the backups
-- cannot restore — restore first, then drop:
--   update work_orders w set equipment_id = null, schedule_id = null,
--     started_at = null, completed_at = null, signed_off_at = null,
--     classification = b.classification
--     from m1_backup_20261026_work_orders b where b.id = w.id;
--   update maintenance_schedules s set equipment_id = null, classification = null,
--     discipline = null, trade = null
--     from m1_backup_20261026_schedules s2 where s2.id = s.id;
--   alter table public.work_orders drop column if exists equipment_id,
--     drop column if exists schedule_id, drop column if exists started_at,
--     drop column if exists completed_at, drop column if exists signed_off_at;
--   alter table public.maintenance_schedules drop column if exists equipment_id,
--     drop column if exists classification, drop column if exists discipline,
--     drop column if exists trade;

begin;

-- Guards: shape must match the review, and this file must not run twice. ----
do $$
declare n int;
begin
  if to_regclass('public.m1_backup_20261026_work_orders') is not null
     or to_regclass('public.m1_backup_20261026_schedules') is not null then
    raise exception 'M1 backup tables already exist: refusing to run twice';
  end if;

  -- M1 columns must all be absent (a re-run, or a partial earlier run).
  select count(*) into n from information_schema.columns
    where table_schema = 'public'
      and ((table_name = 'work_orders' and column_name in
            ('equipment_id','schedule_id','started_at','completed_at','signed_off_at'))
        or (table_name = 'maintenance_schedules' and column_name in
            ('equipment_id','classification','discipline','trade')));
  if n <> 0 then
    raise exception 'M1 guard: % M1 columns already exist (re-run?)', n;
  end if;

  -- Every compressor-routine:NN marker on schedules and work orders must name
  -- a register item that exists, or the backfill would invent a dead link.
  select count(*) into n from (
    select distinct (regexp_match(equipment_info, '^compressor-routine:(\d+)$'))[1]::bigint as ref
    from (select equipment_info from maintenance_schedules
          union select equipment_info from work_orders) u
    where equipment_info ~ '^compressor-routine:\d+$'
  ) m where ref not in (select id from equipment);
  if n <> 0 then
    raise exception 'M1 guard: % marker ids are not on the equipment register', n;
  end if;

  -- Each generated work order must sit on at most one run, or schedule_id is ambiguous.
  select count(*) into n from (
    select work_order_id from maintenance_schedule_runs
    where work_order_id is not null group by work_order_id having count(*) > 1
  ) d;
  if n <> 0 then
    raise exception 'M1 guard: % work orders sit on more than one run', n;
  end if;
end $$;

-- Backups: both tables whole (small: ~176 + ~17 rows), before any change. ---
create table m1_backup_20261026_work_orders as select * from work_orders;
create table m1_backup_20261026_schedules as select * from maintenance_schedules;

do $$
declare n_wo int; n_sched int; live_wo int; live_sched int;
begin
  select count(*) into n_wo from m1_backup_20261026_work_orders;
  select count(*) into live_wo from work_orders;
  select count(*) into n_sched from m1_backup_20261026_schedules;
  select count(*) into live_sched from maintenance_schedules;
  if n_wo <> live_wo or n_sched <> live_sched then
    raise exception 'M1 guard: backup counts wrong (wo=%/% sched=%/%)',
      n_wo, live_wo, n_sched, live_sched;
  end if;
end $$;

-- Section 1: the new columns (all nullable, nothing existing changes). -------
alter table public.work_orders
  add column equipment_id bigint,
  add column schedule_id bigint,
  add column started_at timestamptz,
  add column completed_at timestamptz,
  add column signed_off_at timestamptz;
alter table public.maintenance_schedules
  add column equipment_id bigint,
  add column classification text,
  add column discipline text,
  add column trade text;

-- Section 2: backfill the links, by rule. ------------------------------------
-- 2a. Schedules: marker id first (exact), else exact name match trimmed both
-- sides (the register holds 'Compressor No 1 ' with a trailing space).
update maintenance_schedules s set equipment_id =
  case
    when s.equipment_info ~ '^compressor-routine:(\d+)$'
      then (regexp_match(s.equipment_info, '^compressor-routine:(\d+)$'))[1]::bigint
    else (select e.id from equipment e
          where btrim(e.name) = btrim(s.equipment_info) limit 1)
  end
where s.equipment_id is null
  and (s.equipment_info ~ '^compressor-routine:(\d+)$'
       or exists (select 1 from equipment e where btrim(e.name) = btrim(s.equipment_info)));

-- 2b. Work orders: schedule_id from the runs log.
update work_orders w set schedule_id = r.schedule_id
from maintenance_schedule_runs r
where r.work_order_id = w.id and w.schedule_id is null;

-- 2c. Work orders: equipment via the schedule link first, else own marker,
-- else exact name match. (Runs after 2a/2b so the schedule links exist.)
update work_orders w set equipment_id = coalesce(
  (select s.equipment_id from maintenance_schedules s where s.id = w.schedule_id),
  case when w.equipment_info ~ '^compressor-routine:(\d+)$'
       then (regexp_match(w.equipment_info, '^compressor-routine:(\d+)$'))[1]::bigint end,
  (select e.id from equipment e where btrim(e.name) = btrim(w.equipment_info) limit 1)
)
where w.equipment_id is null;

-- 2d. Generated jobs inherit the planned-maintenance classification (only where
-- none was recorded; a recorded 'breakdown' is never overwritten).
update work_orders set classification = 'planned_maintenance'
where schedule_id is not null and classification is null;

-- 2e. The completed rows: started/completed from artisan_date + recorded times
-- (Harare wall time), signed_off from foreman_date at date precision. Only
-- rows with a parseable HH:MM(:SS) time are stamped; anything else stays NULL
-- rather than guessed.
update work_orders set
  started_at = ((artisan_date::text || ' ' || time_work_started)::timestamp at time zone 'Africa/Harare')
where status = 'completed' and started_at is null
  and artisan_date is not null and time_work_started ~ '^\d{2}:\d{2}(:\d{2})?$';
update work_orders set
  completed_at = ((artisan_date::text || ' ' || time_work_finished)::timestamp at time zone 'Africa/Harare')
where status = 'completed' and completed_at is null
  and artisan_date is not null and time_work_finished ~ '^\d{2}:\d{2}(:\d{2})?$';
update work_orders set
  signed_off_at = (foreman_date::timestamp at time zone 'Africa/Harare')
where status = 'completed' and signed_off_at is null
  and foreman_date is not null and btrim(coalesce(foreman_sign, '')) <> '';

-- Section 3: foreign keys (validated against the backfilled data) + indexes.
-- SET NULL both ways: deleting a machine or a schedule unlinks rows, it never
-- blocks the delete and never cascades it.
alter table public.work_orders
  add constraint fk_work_orders_equipment
    foreign key (equipment_id) references public.equipment(id) on delete set null,
  add constraint fk_work_orders_schedule
    foreign key (schedule_id) references public.maintenance_schedules(id) on delete set null;
alter table public.maintenance_schedules
  add constraint fk_maintenance_schedules_equipment
    foreign key (equipment_id) references public.equipment(id) on delete set null;
create index if not exists idx_work_orders_equipment_id on public.work_orders (equipment_id);
create index if not exists idx_work_orders_schedule_id on public.work_orders (schedule_id);
create index if not exists idx_maintenance_schedules_equipment_id on public.maintenance_schedules (equipment_id);

notify pgrst, 'reload schema';

-- Section 4: report — what linked, and what needs a person. ------------------
do $$
declare
  n_sched int; n_sched_linked int; n_wo int; n_wo_sched int; n_wo_equip int;
  n_class int; n_started int; n_completed int; n_signed int;
begin
  select count(*), count(equipment_id) into n_sched, n_sched_linked from maintenance_schedules;
  select count(*), count(schedule_id), count(equipment_id)
    into n_wo, n_wo_sched, n_wo_equip from work_orders;
  select count(*) filter (where classification = 'planned_maintenance') into n_class from work_orders;
  select count(started_at), count(completed_at), count(signed_off_at)
    into n_started, n_completed, n_signed from work_orders;
  raise notice 'M1 OK: schedules % (% linked), work orders % (% with schedule, % with machine), planned_maintenance %, started % / completed % / signed %',
    n_sched, n_sched_linked, n_wo, n_wo_sched, n_wo_equip, n_class, n_started, n_completed, n_signed;
end $$;

-- The rest: machine names that matched nothing (resolve by hand — usually a
-- typo like 'hbub', or a machine missing from the register).
select w.equipment_info as unmatched_machine, count(*) as work_orders
from work_orders w
where w.equipment_id is null and btrim(coalesce(w.equipment_info, '')) <> ''
group by w.equipment_info order by count(*) desc;
select s.id as schedule_id, s.name, s.equipment_info as unmatched_machine
from maintenance_schedules s
where s.equipment_id is null and btrim(coalesce(s.equipment_info, '')) <> ''
  and s.equipment_info !~ '^compressor-routine:\d+$';

commit;
