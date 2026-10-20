-- PPE: the six records that needed the owner's decision (RAN 20 Oct 2026 by the assistant, in two steps; record of what was run).
-- Follows supabase_fix_ppe_returned_by_order_list_2026-10-20.sql, which fixed the 11 clean cases.
--
-- Owner's answers: Rukudzo, Valentine, Dorica and Nemai each got ONE new worksuit on 6 Oct (the extra copies were double entries); Nemai's
-- 3 Sep suit was replaced by it; Shepherd's new suit is size 50; Takudzwa got ONE pair of shoes on 6 Oct.
--
-- Each old record (24, 159, 155, 225, 152, 291) is renewed in place from the copy that was kept (419, 418, 416, 417, 429, 430): new issue
-- date and expiry, status active, name as in the personnel register; its own notes, issued-by and location kept. All 14 copies (the kept
-- ones included) were then deleted. Both steps are guarded (they change nothing unless exactly 6 records and 14 copies match) and every
-- row was first copied to ppe_records_backup_2026_10_20. Writes made here bypass the app, so they are not in the audit log.

-- STEP 1: backup and renew (no delete)
begin;
create temp table _m (r_id bigint, k_id bigint) on commit drop;
insert into _m values (24,419),(159,418),(155,416),(225,417),(152,429),(291,430);
create temp table _c (r_id bigint, c_id bigint) on commit drop;
insert into _c values (24,419),(24,421),(24,422),(159,418),(159,420),(159,425),(155,416),(155,424),(225,417),(225,423),(152,428),(152,429),(291,430),(291,434);

do $$
declare pairs_ok int; copies_ok int;
begin
  select count(*) into pairs_ok from _m m join ppe_records r on r.id = m.r_id join ppe_records k on k.id = m.k_id
   where r.status = 'returned' and k.status = 'active' and r.employee_id = k.employee_id and lower(trim(r.ppe_type)) = lower(trim(k.ppe_type));
  select count(*) into copies_ok from _c c join ppe_records r on r.id = c.r_id join ppe_records x on x.id = c.c_id
   where x.status = 'active' and x.employee_id = r.employee_id and lower(trim(x.ppe_type)) = lower(trim(r.ppe_type)) and x.updated_at - x.created_at < interval '10 seconds';
  if pairs_ok <> 6 or copies_ok <> 14 then
    raise exception 'Expected 6 records and 14 copies, found % and %. Nothing was changed.', pairs_ok, copies_ok;
  end if;
end $$;

insert into ppe_records_backup_2026_10_20
select p.* from ppe_records p
where p.id in (select r_id from _c union select c_id from _c)
  and not exists (select 1 from ppe_records_backup_2026_10_20 b where b.id = p.id and b.updated_at = p.updated_at);

update ppe_records r set
  employee_name = k.employee_name, issue_date = k.issue_date, expiry_date = k.expiry_date, size = k.size, item_name = k.item_name,
  condition = k.condition, status = 'active',
  notes = coalesce(nullif(k.notes, ''), r.notes), issued_by = coalesce(nullif(k.issued_by, ''), r.issued_by),
  location = coalesce(nullif(k.location, ''), r.location), position = coalesce(nullif(k.position, ''), r.position),
  mine_section = coalesce(nullif(k.mine_section, ''), r.mine_section), updated_at = now()
from _m m join ppe_records k on k.id = m.k_id
where r.id = m.r_id;
commit;

-- STEP 2: remove the 14 copies (only those in the backup, and only while their renewed record is active)
begin;
do $$
declare removed int;
begin
  with c(r_id, c_id) as (values (24,419),(24,421),(24,422),(159,418),(159,420),(159,425),(155,416),(155,424),(225,417),(225,423),(152,428),(152,429),(291,430),(291,434)),
  d as (
    delete from ppe_records x using c, ppe_records r
    where x.id = c.c_id and r.id = c.r_id and r.status = 'active' and x.status = 'active'
      and r.employee_id = x.employee_id and lower(trim(r.ppe_type)) = lower(trim(x.ppe_type))
      and exists (select 1 from ppe_records_backup_2026_10_20 b where b.id = x.id and b.updated_at = x.updated_at)
    returning x.id
  )
  select count(*) into removed from d;
  if removed <> 14 then raise exception 'Expected to remove 14 copies, matched %. Nothing was changed.', removed; end if;
end $$;
commit;

-- STEP 3 (RAN 20 Oct 2026, owner: "leave one copy, time doesn't matter as long as it is the same date"): two more same-day duplicates.
-- Kept 368 (Malack Musasa, pneumo jacket; 369 was identical) and 27 (Joel Sezi, worksuit; the later-edited of the pair, 26 removed).
-- Backed up first (ppe_records_backup_2026_10_20, now 46 rows), then deleted 369 and 26 under a guard that matches exactly 2.
-- NOT touched: Takudzwa Shangwa 293 "Cap lamp belt" and 294 "Safety Chain", both typed Cap_lamp_belt on 2026-04-08. They are two different
-- items, not a duplicate; 294 probably should be type safety_chain_belt (also a 24-month item). Left for the owner.
