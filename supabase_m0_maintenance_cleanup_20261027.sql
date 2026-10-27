-- M0 maintenance cleanup, REGENERATED 27 Oct 2026 (supersedes the 20261026 file,
-- kept for the record; run THIS file, not that one).
--
-- WHAT CHANGED since the 26 Oct review: the owner deleted 25 work orders via the
-- app on 27 Oct ~04:11 UTC (all in deleted_records + delete-audit events):
--   375-382 (incl. the 4 designated keepers 375/376/379/381 AND del-list
--     377/378/380/382), 385, 394-407 (14 fresh cyct cron rows), 410, 422.
-- Live at regen time: 200 work orders (198 pending), 17 schedules, 178 runs,
-- max id 431, nothing newer. This file regenerates the lists rather than forcing:
--   1. Deletes 116 remaining test work orders (120 minus owner-deleted
--      377/378/380/382), their runs, and test schedules 5 and 6. Runs on 5/6
--      are 117 (111 linked + 6 orphans pointing at owner-deleted
--      377/378/380/382/394/407); all 6 orphan run ids are pinned below.
--   2. Marks the same 35 missed routine jobs "not-done" (all still present).
--      The newest-per-schedule keepers are gone (owner-deleted); the next-
--      newest (354/349/352) are NOT promoted to pending — the owner removed
--      the newest of each, signalling a clean slate, and the cron raises
--      fresh jobs. Recorded as release assumption R1.
--   3. Sets the real machine name on 13 compressor rows (14 minus
--      owner-deleted 376).
--   4. Deletes the 32 remaining leaked M4 test rows (35 minus owner-deleted
--      385/410/422), each fingerprinted by its "Raised from the breakdown"
--      note; the guard aborts unless all 32 match.
--
-- SAFE TO RUN: one transaction; backup tables first; guards stop it without
-- changing anything unless the data still looks exactly like this review.
-- The events guard ignores action='deleted' rows (the owner's delete audit).
-- Writes here do not go through the app, so they are not in the audit log;
-- the backup tables are the record. Run it in the Supabase SQL editor.
--
-- RUN ORDER: needs the "not-done" status wiring live (M2, released 27 Oct —
-- run the whole file at once). Re-running changes nothing (backup-exists
-- guard + idempotent counts).
--
-- ROLLBACK (same editor, before dropping backups): re-insert deleted rows from
-- the three m0_backup_20261027_* tables (schedules, then work orders, then
-- runs), and restore updated columns with:
--   update work_orders w set status = b.status, equipment_info = b.equipment_info,
--     equipment = b.equipment from m0_backup_20261027_work_orders b where b.id = w.id;

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
  (353), (355), (356), (369), (370), (371), (374),
  (213), (223), (234), (244), (254), (265), (274), (284), (294), (305),
  (317), (328), (335), (340), (351), (373);

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
  (372, 'compressor-routine:35', 'Compressor No 10');

create temp table _m0_leaked (id bigint primary key) on commit drop;
insert into _m0_leaked (id) values
  (383), (384),
  (386), (387), (388), (389), (390), (391), (392), (393),
  (408), (409),
  (411), (412), (413), (414), (415), (416), (417), (418),
  (419),
  (420), (421),
  (423), (424), (425), (426), (427), (428), (429), (430),
  (431);

-- Guards: the data must still match this review exactly. -------------------
do $$
declare
  n int;
begin
  if to_regclass('public.m0_backup_20261027_work_orders') is not null
     or to_regclass('public.m0_backup_20261027_runs') is not null
     or to_regclass('public.m0_backup_20261027_schedules') is not null then
    raise exception 'M0 backup tables already exist: refusing to run twice';
  end if;

  -- 0. no drift since the regen probe (200 orders, max id 431)
  select count(*) into n from work_orders;
  if n <> 200 then
    raise exception 'M0 guard: expected 200 work orders, found %', n;
  end if;
  select max(id) into n from work_orders;
  if n <> 431 then
    raise exception 'M0 guard: expected max id 431, found %', n;
  end if;

  -- 1. delete list: 116 rows, all still present and pending
  select count(*) into n from work_orders w join _m0_del_wo d on d.id = w.id
    where w.status = 'pending';
  if n <> 116 then
    raise exception 'M0 guard: expected 116 pending listed work orders, found %', n;
  end if;
  -- no listed id may have grown non-delete comments or event rows since review
  -- (action='deleted' rows are the owner's delete audit and are expected)
  select count(*) into n from work_order_comments c join _m0_del_wo d on d.id = c.work_order_id;
  if n <> 0 then
    raise exception 'M0 guard: % comments now exist on listed rows', n;
  end if;
  select count(*) into n from maintenance_events e join _m0_del_wo d on d.id = e.entity_id
    where e.entity = 'work_order' and e.action <> 'deleted';
  if n <> 0 then
    raise exception 'M0 guard: % non-delete event rows now exist on listed rows', n;
  end if;

  -- 2. runs of the two test schedules: exactly 117 — 111 linked to the delete
  --    list plus 6 orphan runs pointing at owner-deleted rows (run ids
  --    160/161/163/165 -> WOs 377/378/380/382, 166/179 -> WOs 394/407)
  select count(*) into n from maintenance_schedule_runs r
    where r.schedule_id in (5, 6);
  if n <> 117 then
    raise exception 'M0 guard: expected 117 runs on schedules 5/6, found %', n;
  end if;
  select count(*) into n from maintenance_schedule_runs r
    where r.schedule_id in (5, 6)
      and r.id not in (160, 161, 163, 165, 166, 179)
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

  -- 5. rename list: 13 rows, equipment_info still exactly the markers
  select count(*) into n from work_orders w join _m0_rename r on r.id = w.id
    where w.equipment_info = r.marker and (w.equipment = r.marker or w.equipment is null);
  if n <> 13 then
    raise exception 'M0 guard: expected 13 marker rows, found %', n;
  end if;

  -- 6. leaked list: 32 rows, all present, pending, and fingerprinted
  select count(*) into n from work_orders w join _m0_leaked l on l.id = w.id
    where w.status = 'pending' and w.notes like '%Raised from the breakdown%';
  if n <> 32 then
    raise exception 'M0 guard: expected 32 fingerprinted leaked rows, found %', n;
  end if;
end $$;

-- Backups: every touched row, whole row, before any change. -----------------
create table m0_backup_20261027_work_orders as
  select w.* from work_orders w
  where w.id in (select id from _m0_del_wo)
     or w.id in (select id from _m0_notdone)
     or w.id in (select id from _m0_rename)
     or w.id in (select id from _m0_leaked);
create table m0_backup_20261027_runs as
  select r.* from maintenance_schedule_runs r where r.schedule_id in (5, 6);
create table m0_backup_20261027_schedules as
  select s.* from maintenance_schedules s where s.id in (5, 6);

do $$
declare n_wo int; n_runs int; n_sched int;
begin
  select count(*) into n_wo from m0_backup_20261027_work_orders;
  select count(*) into n_runs from m0_backup_20261027_runs;
  select count(*) into n_sched from m0_backup_20261027_schedules;
  -- 116 deletes + 35 not-done + 13 renames + 32 leaked, minus 366/368 which are in both update lists
  if n_wo <> 194 or n_runs <> 117 or n_sched <> 2 then
    raise exception 'M0 guard: backup counts wrong (wo=%, runs=%, sched=%)', n_wo, n_runs, n_sched;
  end if;
end $$;

-- Section 1: deletes (runs first so no foreign key can complain). -----------
do $$
declare n int;
begin
  delete from maintenance_schedule_runs where schedule_id in (5, 6);
  get diagnostics n = row_count;
  if n <> 117 then raise exception 'M0: deleted % runs, expected 117', n; end if;

  delete from work_orders where id in (select id from _m0_del_wo);
  get diagnostics n = row_count;
  if n <> 116 then raise exception 'M0: deleted % work orders, expected 116', n; end if;

  delete from work_orders where id in (select id from _m0_leaked);
  get diagnostics n = row_count;
  if n <> 32 then raise exception 'M0: deleted % leaked rows, expected 32', n; end if;

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
  if n <> 13 then raise exception 'M0: renamed % rows, expected 13', n; end if;
end $$;

-- End-state check: 15 pending, 35 not-done, 2 completed; 15 schedules; 61 runs.
do $$
declare p int; nd int; c int; s int; r int;
begin
  select count(*) filter (where status = 'pending') into p from work_orders;
  select count(*) filter (where status = 'not-done') into nd from work_orders;
  select count(*) filter (where status = 'completed') into c from work_orders;
  select count(*) into s from maintenance_schedules;
  select count(*) into r from maintenance_schedule_runs;
  if p <> 15 or nd <> 35 or c <> 2 or s <> 15 or r <> 61 then
    raise exception 'M0: end state wrong (pending=%, not-done=%, completed=%, sched=%, runs=%)', p, nd, c, s, r;
  end if;
  raise notice 'M0 OK: pending=15 not-done=35 completed=2 schedules=15 runs=61';
end $$;

commit;
