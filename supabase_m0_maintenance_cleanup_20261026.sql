-- M0 maintenance cleanup (owner-run, reviewed 26 Oct 2026). NOT RUN YET.
--
-- WHAT THIS DOES, exactly the rows in audit/maintenance-2026-10-26/M0_CLEANUP_LIST.md
-- (every id verified read-only against live on 26 Oct 2026: 176 work orders,
-- 17 schedules, 164 runs, 0 comments, 1 event row on kept WO-00170):
--   1. Deletes 120 test work orders ("cyct" x100 + seed 206, "jbb" x15 + 215/335,
--      duplicates 208/373), their 115 schedule runs, and test schedules 5 and 6.
--   2. Marks 35 missed routine jobs "not-done" (older past-due generated rows only;
--      the newest open job per schedule stays pending; completed rows untouched).
--   3. Sets the real machine name on 14 compressor work orders that read
--      "compressor-routine:NN". Schedules keep their markers (the compressor
--      auto-scheduler matches on them; M1 replaces markers with a register link).
--
-- SAFE TO RUN: one transaction; backup tables first; guards stop it without
-- changing anything unless the data still looks exactly like the review list
-- (re-probe and regenerate this file if the daily cron raised new rows since).
-- Writes here do not go through the app, so they are not in the audit log;
-- the backup tables are the record. Run it in the Supabase SQL editor.
--
-- RUN ORDER: section 2 needs the app to know the "not-done" status first
-- (backend overdue/stats + frontend filter/tiles, wired in M2). Either run the
-- whole file after that code is live, or run sections 1+3 now and section 2
-- later (each guarded section is safe on its own; re-running changes nothing).
--
-- ROLLBACK (same editor, before dropping backups): re-insert deleted rows from
-- the three m0_backup_20261026_* tables (runs first... actually schedules,
-- then work orders, then runs, to satisfy any foreign key), and restore updated
-- columns with:
--   update work_orders w set status = b.status, equipment_info = b.equipment_info,
--     equipment = b.equipment from m0_backup_20261026_work_orders b where b.id = w.id;

begin;

-- The exact review lists. -------------------------------------------------
create temp table _m0_del_wo (id bigint primary key) on commit drop;
insert into _m0_del_wo (id) values
  (206),
  (208), (215),
  (212), (217), (218), (219), (220), (222), (224), (226), (227), (229),
  (230), (231), (233), (235), (237), (238), (239), (240), (241), (243),
  (245), (247), (248), (249), (250), (251), (253), (255), (257), (258),
  (259), (260), (261), (263), (264), (266), (268), (269), (270), (271),
  (273), (275), (277), (278), (279), (280), (281), (283), (285), (287),
  (288), (289), (290), (291), (293), (295), (297), (298), (300), (301),
  (302), (304), (307), (309), (310), (311), (312), (313), (315), (316),
  (319), (322), (323), (324), (325), (327), (330), (332), (333), (334),
  (336), (337), (339), (342), (344), (345), (346), (347), (348), (350),
  (353), (355), (356), (369), (370), (371), (374), (377), (380), (382),
  (213), (223), (234), (244), (254), (265), (274), (284), (294), (305),
  (317), (328), (335), (340), (351), (373), (378);

create temp table _m0_del_sched (id bigint primary key) on commit drop;
insert into _m0_del_sched (id) values (5), (6);

create temp table _m0_notdone (id bigint primary key) on commit drop;
insert into _m0_notdone (id) values
  (211), (221), (225), (232), (236), (242), (246), (252), (256), (262),
  (267), (272), (276), (282), (286), (292), (296), (299), (303), (306),
  (308), (314), (318), (320), (326), (329), (331), (338), (341), (343),
  (349), (352), (354), (366), (368);

create temp table _m0_rename (id bigint primary key, marker text not null, name text not null) on commit drop;
insert into _m0_rename (id, marker, name) values
  (357, 'compressor-routine:28', 'Compressor No 3'),
  (358, 'compressor-routine:29', 'Compressor No 4'),
  (359, 'compressor-routine:26', 'Compressor No 1 '),
  (360, 'compressor-routine:27', 'Compressor No 2'),
  (361, 'compressor-routine:30', 'Compressor No 5'),
  (362, 'compressor-routine:31', 'Compressor No 6'),
  (363, 'compressor-routine:32', 'Compressor No 7'),
  (364, 'compressor-routine:33', 'Compressor No 8'),
  (365, 'compressor-routine:34', 'Compressor No 9'),
  (366, 'compressor-routine:35', 'Compressor No 10'),
  (367, 'compressor-routine:36', 'Compressor No 11'),
  (368, 'compressor-routine:37', 'Compressor No 12'),
  (372, 'compressor-routine:35', 'Compressor No 10'),
  (376, 'compressor-routine:37', 'Compressor No 12');

-- Guards: the data must still match the review list exactly. ----------------
do $$
declare
  n int;
begin
  if to_regclass('public.m0_backup_20261026_work_orders') is not null
     or to_regclass('public.m0_backup_20261026_runs') is not null
     or to_regclass('public.m0_backup_20261026_schedules') is not null then
    raise exception 'M0 backup tables already exist: refusing to run twice';
  end if;

  -- 1. delete list: 120 rows, all still present and pending
  select count(*) into n from work_orders w join _m0_del_wo d on d.id = w.id
    where w.status = 'pending';
  if n <> 120 then
    raise exception 'M0 guard: expected 120 pending listed work orders, found %', n;
  end if;
  -- no listed id may have grown comments or event rows since the review
  select count(*) into n from work_order_comments c join _m0_del_wo d on d.id = c.work_order_id;
  if n <> 0 then
    raise exception 'M0 guard: % comments now exist on listed rows', n;
  end if;
  select count(*) into n from maintenance_events e join _m0_del_wo d on d.id = e.entity_id
    where e.entity = 'work_order';
  if n <> 0 then
    raise exception 'M0 guard: % event rows now exist on listed rows', n;
  end if;

  -- 2. runs of the two test schedules: exactly 115, none null, all pointing
  --    at the delete list (fails if the cron raised fresh rows since review)
  select count(*) into n from maintenance_schedule_runs r
    where r.schedule_id in (5, 6);
  if n <> 115 then
    raise exception 'M0 guard: expected 115 runs on schedules 5/6, found %', n;
  end if;
  select count(*) into n from maintenance_schedule_runs r
    where r.schedule_id in (5, 6)
      and (r.work_order_id is null or r.work_order_id not in (select id from _m0_del_wo));
  if n <> 0 then
    raise exception 'M0 guard: % runs on schedules 5/6 fall outside the delete list (fresh cron rows?)', n;
  end if;

  -- 3. schedules 5 and 6 still exist (names unchecked: a rename is harmless)
  select count(*) into n from maintenance_schedules s join _m0_del_sched d on d.id = s.id;
  if n <> 2 then
    raise exception 'M0 guard: expected test schedules 5 and 6, found %', n;
  end if;

  -- 4. not-done list: 35 rows, all still present and pending
  select count(*) into n from work_orders w join _m0_notdone d on d.id = w.id
    where w.status = 'pending';
  if n <> 35 then
    raise exception 'M0 guard: expected 35 pending listed work orders, found %', n;
  end if;

  -- 5. rename list: 14 rows, equipment_info still exactly the markers
  --    (equipment is the marker on the 3 manual raises, NULL on generated rows)
  select count(*) into n from work_orders w join _m0_rename r on r.id = w.id
    where w.equipment_info = r.marker and (w.equipment = r.marker or w.equipment is null);
  if n <> 14 then
    raise exception 'M0 guard: expected 14 marker rows, found %', n;
  end if;
end $$;

-- Backups: every touched row, whole row, before any change. -----------------
create table m0_backup_20261026_work_orders as
  select w.* from work_orders w
  where w.id in (select id from _m0_del_wo)
     or w.id in (select id from _m0_notdone)
     or w.id in (select id from _m0_rename);
create table m0_backup_20261026_runs as
  select r.* from maintenance_schedule_runs r where r.schedule_id in (5, 6);
create table m0_backup_20261026_schedules as
  select s.* from maintenance_schedules s where s.id in (5, 6);

do $$
declare n_wo int; n_runs int; n_sched int;
begin
  select count(*) into n_wo from m0_backup_20261026_work_orders;
  select count(*) into n_runs from m0_backup_20261026_runs;
  select count(*) into n_sched from m0_backup_20261026_schedules;
  -- 120 deletes + 35 not-done + 14 renames, minus 366/368 which are in both update lists
  if n_wo <> 167 or n_runs <> 115 or n_sched <> 2 then
    raise exception 'M0 guard: backup counts wrong (wo=%, runs=%, sched=%)', n_wo, n_runs, n_sched;
  end if;
end $$;

-- Section 1: deletes (runs first so no foreign key can complain). -----------
do $$
declare n int;
begin
  delete from maintenance_schedule_runs where schedule_id in (5, 6);
  get diagnostics n = row_count;
  if n <> 115 then raise exception 'M0: deleted % runs, expected 115', n; end if;

  delete from work_orders where id in (select id from _m0_del_wo);
  get diagnostics n = row_count;
  if n <> 120 then raise exception 'M0: deleted % work orders, expected 120', n; end if;

  delete from maintenance_schedules where id in (select id from _m0_del_sched);
  get diagnostics n = row_count;
  if n <> 2 then raise exception 'M0: deleted % schedules, expected 2', n; end if;
end $$;

-- Section 2: missed routine jobs become "not-done". --------------------------
do $$
declare n int;
begin
  update work_orders set status = 'not-done' where id in (select id from _m0_notdone);
  get diagnostics n = row_count;
  if n <> 35 then raise exception 'M0: marked % not-done, expected 35', n; end if;
end $$;

-- Section 3: compressor rows show the machine name. --------------------------
do $$
declare n int;
begin
  update work_orders w set equipment_info = r.name, equipment = r.name
    from _m0_rename r where r.id = w.id;
  get diagnostics n = row_count;
  if n <> 14 then raise exception 'M0: renamed % rows, expected 14', n; end if;
end $$;

-- End-state check: 19 pending, 35 not-done, 2 completed; 15 schedules; 49 runs.
do $$
declare p int; nd int; c int; s int; r int;
begin
  select count(*) filter (where status = 'pending') into p from work_orders;
  select count(*) filter (where status = 'not-done') into nd from work_orders;
  select count(*) filter (where status = 'completed') into c from work_orders;
  select count(*) into s from maintenance_schedules;
  select count(*) into r from maintenance_schedule_runs;
  if p <> 19 or nd <> 35 or c <> 2 or s <> 15 or r <> 49 then
    raise exception 'M0: end state wrong (pending=%, not-done=%, completed=%, sched=%, runs=%)', p, nd, c, s, r;
  end if;
  raise notice 'M0 OK: pending=19 not-done=35 completed=2 schedules=15 runs=49';
end $$;

commit;
