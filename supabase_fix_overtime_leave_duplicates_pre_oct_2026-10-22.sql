-- Overtime/leaves: resolve the pre-October exact duplicates (owner instruction
-- 22 Oct 2026: "for overtimes and leaves - those that are before 1 October you
-- can fix the duplicates"). Follows the per-file prescription: keep one copy of
-- each group (the oldest id), set overtime extras to 'cancelled', leave extras
-- to 'rejected'. NOT touched here: the post-October overtime group (C1750,
-- 2026-10-16, 3 pending) and the 2 PPE groups (out of scope for this fix).
--
-- PAYROLL NOTE: all 28 resolved copies were APPROVED in closed months (Nov 25 -
-- Aug 26, heavy cluster 2026-03-04) and may have been counted twice (21 overtime
-- copies incl. three 3-copy groups; 7 leave copies). Cancelling corrects reports
-- going forward; it does not unpay. Full rows are kept in
-- dedup_overtime_backup_2026_10_22 / dedup_leaves_backup_2026_10_22.
--
-- RAN 2026-10-22 via scripts/apply_migration.py, then recorded in the ledger.
-- Safe to re-run: the guards abort unless the exact 28 ids are still approved
-- with their keeper twins live (second run finds 0 and aborts, changing nothing).

begin;

do $$
declare
  v_ot int; v_lv int; v_ot_twin int; v_lv_twin int;
begin
  select count(*) into v_ot from public.overtime
  where id = any (array[29,49,48,118,710,299,307,399,433,303,403,398,301,405,305,401,306,400,700,872,1280])
    and status = 'approved';
  if v_ot <> 21 then
    raise exception 'Expected the 21 overtime duplicate ids to still be approved, found %. Nothing was changed.', v_ot;
  end if;
  select count(*) into v_lv from public.leaves
  where id = any (array[184,218,231,227,228,229,230]) and status = 'approved';
  if v_lv <> 7 then
    raise exception 'Expected the 7 leave duplicate ids to still be approved, found %. Nothing was changed.', v_lv;
  end if;
  select count(*) into v_ot_twin from public.overtime c
  where c.id = any (array[29,49,48,118,710,299,307,399,433,303,403,398,301,405,305,401,306,400,700,872,1280])
    and exists (
      select 1 from public.overtime k
      where k.id = any (array[27,41,42,81,89,204,213,286,189,214,187,211,212,586,845,1279])
        and k.status = 'approved' and k.employee_id = c.employee_id and k.date = c.date
        and k.start_time = c.start_time and k.end_time = c.end_time);
  if v_ot_twin <> 21 then
    raise exception 'Expected all 21 overtime rows to still have a live keeper twin, found %. Nothing was changed.', v_ot_twin;
  end if;
  select count(*) into v_lv_twin from public.leaves c
  where c.id = any (array[184,218,231,227,228,229,230])
    and exists (
      select 1 from public.leaves k
      where k.id = any (array[167,214,176,212,213,211,210])
        and k.status = 'approved' and k.employee_id = c.employee_id
        and k.start_date = c.start_date and k.end_date = c.end_date);
  if v_lv_twin <> 7 then
    raise exception 'Expected all 7 leave rows to still have a live keeper twin, found %. Nothing was changed.', v_lv_twin;
  end if;
end $$;

create table public.dedup_overtime_backup_2026_10_22 as
select * from public.overtime
where id = any (array[29,49,48,118,710,299,307,399,433,303,403,398,301,405,305,401,306,400,700,872,1280]);

create table public.dedup_leaves_backup_2026_10_22 as
select * from public.leaves
where id = any (array[184,218,231,227,228,229,230]);

update public.overtime set status = 'cancelled', updated_at = now()
where id = any (array[29,49,48,118,710,299,307,399,433,303,403,398,301,405,305,401,306,400,700,872,1280]);

update public.leaves set status = 'rejected', updated_at = now()
where id = any (array[184,218,231,227,228,229,230]);

commit;

-- Verify afterwards (expect 1 overtime group, 0 leave groups):
-- select employee_id, date, count(*), array_agg(id order by id) from public.overtime
-- where start_time is not null and end_time is not null
--   and coalesce(status,'') not in ('rejected','cancelled')
-- group by employee_id, date, start_time, end_time having count(*) > 1;
-- select employee_id, start_date, end_date, count(*), array_agg(id order by id) from public.leaves
-- where status <> 'rejected' group by employee_id, start_date, end_date having count(*) > 1;
