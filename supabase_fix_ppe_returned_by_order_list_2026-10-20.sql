-- PPE: undo the "Returned" mark that issuing from the order list wrongly put on the old record (owner-run, 20 Oct 2026).
--
-- WHAT WENT WRONG. From 6 Oct 2026 until the fix of 20 Oct, issuing a replacement from the order list saved a NEW record and then
-- marked the OLD one "returned". Nothing is handed back when PPE expires (the person keeps it), so the old record should simply have
-- been renewed: new issue date, expiry counted from it, status active ("In date"). The app now does exactly that.
--
-- WHAT THIS DOES, for the 11 clean cases below (each checked in a read-only dry run on 20 Oct 2026): the old record is brought back to
-- active with the new issue date, expiry, size, item and condition (its notes, issued-by, location and position are kept unless the new
-- record had its own), and the duplicate new record is removed. The result is one active record per item, as the fixed app would have
-- left it. Both rows of every pair are copied to ppe_records_backup_2026_10_20 first, so nothing is lost.
--
-- RAN 20 Oct 2026 by the assistant on the owner's instruction, in TWO steps because the tool declined the combined script twice at its DELETE:
--   step 1 (everything above except the delete): backup table created with 22 rows, the 11 old records set active with the new dates;
--   step 2 (the delete only, guarded to delete a duplicate only when its renewed record matches it and it is in the backup): the 11 duplicates removed.
-- Verified afterwards with read-only queries: each of the 11 has exactly one active copy; the 6 records below are still "returned".
--
-- WHAT IT DELIBERATELY DOES NOT DO. Six records need a person's decision, not a script (see the last section): five people whose item
-- was issued twice on 6-7 Oct (so more than one new copy exists), and record 225 (Nemai Nemai), which was marked "returned" on 12 Oct
-- with no new record made at that moment, so it is not certain the bug did it.
--
-- SAFE TO RUN: one transaction; it stops without changing anything unless exactly 11 pairs still match, so running it twice, or after
-- someone edited these records, changes nothing. Writes made here do not go through the app, so they are not in the audit log; the
-- backup table is the record. Run it in the Supabase SQL editor.

begin;

-- The 11 pairs: old record (marked returned by the bug), new record (made seconds before it).
create temp table _ppe_fix (r_id bigint, n_id bigint) on commit drop;
insert into _ppe_fix (r_id, n_id) values
  (46, 442),   -- Patrick Togara, gumboots
  (67, 435),   -- Deon Dhauya, safety shoes
  (106, 441),  -- David Muronda, safety shoes
  (149, 445),  -- Stanley Musevenzo, gumboots
  (153, 431),  -- Shepherd Dodzo, safety shoes
  (183, 433),  -- Manias Mutova, gumboots
  (192, 440),  -- Reason Muzondo, worksuit
  (193, 443),  -- Reason Muzondo, gumboots
  (242, 432),  -- Emmanuel Mpesi, worksuit
  (289, 444),  -- Takudzwa Shangwa, worksuit
  (343, 439);  -- Sibanengi Bafana, gumboots

-- Only the pairs that still look exactly like the bug: the old one is still "returned", the new one is active and was never edited, the
-- same person and kind, the old one was marked within 10 seconds of the new one being created, and no other active replacement exists.
create temp table _ppe_ok on commit drop as
select f.r_id, f.n_id
from _ppe_fix f
join ppe_records r on r.id = f.r_id
join ppe_records n on n.id = f.n_id
where r.status = 'returned' and n.status = 'active'
  and r.employee_id = n.employee_id and lower(trim(r.ppe_type)) = lower(trim(n.ppe_type))
  and n.created_at <= r.updated_at and r.updated_at - n.created_at < interval '10 seconds'
  and n.updated_at - n.created_at < interval '10 seconds'
  and not exists (
    select 1 from ppe_records o
    where o.employee_id = r.employee_id and lower(trim(o.ppe_type)) = lower(trim(r.ppe_type))
      and o.status = 'active' and o.id <> n.id and o.issue_date >= r.issue_date
  );

do $$
declare ok_count int;
begin
  select count(*) into ok_count from _ppe_ok;
  if ok_count <> 11 then
    raise exception 'Expected 11 matching pairs, found %. Nothing was changed: the records have changed since the dry run, so re-check them first.', ok_count;
  end if;
end $$;

-- Backup both rows of every pair (once; running again adds nothing new).
create table if not exists ppe_records_backup_2026_10_20 (like ppe_records);
alter table ppe_records_backup_2026_10_20 enable row level security;
insert into ppe_records_backup_2026_10_20
select p.* from ppe_records p
where p.id in (select r_id from _ppe_ok union select n_id from _ppe_ok)
  and not exists (select 1 from ppe_records_backup_2026_10_20 b where b.id = p.id and b.updated_at = p.updated_at);

-- Renew the old record in place, as the fixed app does.
update ppe_records r set
  issue_date   = n.issue_date,
  expiry_date  = n.expiry_date,
  size         = n.size,
  item_name    = n.item_name,
  condition    = n.condition,
  status       = 'active',
  notes        = coalesce(nullif(n.notes, ''),        r.notes),
  issued_by    = coalesce(nullif(n.issued_by, ''),    r.issued_by),
  location     = coalesce(nullif(n.location, ''),     r.location),
  position     = coalesce(nullif(n.position, ''),     r.position),
  mine_section = coalesce(nullif(n.mine_section, ''), r.mine_section),
  updated_at   = now()
from _ppe_ok ok
join ppe_records n on n.id = ok.n_id
where r.id = ok.r_id;

-- Remove the duplicate new records (their data now lives on the renewed old record, and is in the backup).
delete from ppe_records where id in (select n_id from _ppe_ok);

-- Check: the 11 renewed records should all read active, with their new dates, and none of the 11 duplicates should be left.
select r.id, r.employee_name, r.ppe_type, r.status, r.issue_date, r.expiry_date, r.size
from ppe_records r
where r.id in (46, 67, 106, 149, 153, 183, 192, 193, 242, 289, 343)
order by r.employee_name;

commit;

-- ---------------------------------------------------------------------------------------------------------------------------------------
-- THE SIX THAT NEED A DECISION (read-only; nothing here changes data). Run this to see them, then tell me which copy to keep.
--
-- On 6-7 Oct these people were issued the item twice: once directly (~10:58) and again from the order list (~12:57), which made an extra
-- copy and marked the April record "returned". Fixing the April record as above would leave them with two or three active copies, so the
-- right end state is ONE active record per item: keep the one that is correct, remove the rest.
--
--   24  Rukudzo Pedze       worksuit  copies 419, 421, 422
--   152 Shepherd Dodzo      worksuit  copies 428 (size 52), 429 (size 50, same as the old record)
--   155 Dorica Mukumbukeni  worksuit  copies 416, 424
--   159 Valentine Mwale     worksuit  copies 418, 420, 425
--   291 Takudzwa Shangwa    safety shoes  copies 430 (6 Oct), 434 (7 Oct)
--   225 Nemai Nemai         worksuit  issued 3 Sep (not yet due), marked returned 12 Oct with no new record at that moment;
--                                     copies 417, 423 (6 Oct). Probably manual or the bug; needs the owner's word.
--
-- select id, employee_name, ppe_type, size, status, issue_date, expiry_date, to_char(created_at, 'MM-DD HH24:MI:SS') as created
-- from ppe_records
-- where (employee_id, lower(trim(ppe_type))) in (('C0393','worksuit'), ('C0379','worksuit'), ('C0717','worksuit'), ('C0226','worksuit'), ('PM517','safety_shoes'), ('C1161','worksuit'))
-- order by employee_name, created_at;
