-- PPE: align the stored person copies (name, position, department, section) to the employee register.
-- RUN 2026-10-22 via scripts/apply_migration.py (HTTP 201): 359 rows aligned,
-- drift preview 359 -> 0, backup table ppe_records_backup_2026_10_21 holds 359 rows.
-- Recorded in schema_migrations the same day. Do not re-run (fails loudly by design).
--
-- Why: issues used to stamp whatever the form held (and every save forced department 'MAINTENANCE'), so the copies
-- drifted from the register. From U4.2 the server stamps them on create and on a move; this aligns the existing rows
-- that differ (analysed 2026-10-22, read-only: 359 rows — 23 names, 197 positions, 359 departments, 286 sections).
--
-- PREVIEW (read-only — run first; expect 359 rows):
--   with reg as (
--     select employee_id, first_name, last_name, designation, department, section,
--       row_number() over (partition by upper(trim(employee_id)) order by id) as rn
--     from employees
--     where trim(coalesce(employee_id, '')) <> ''
--   )
--   select r.id, r.employee_id,
--     r.employee_name as old_name,
--     trim(regexp_replace(e.first_name || ' ' || e.last_name, E'\\s+', ' ', 'g')) as new_name,
--     r.position as old_position, coalesce(e.designation, '') as new_position,
--     r.department as old_department, coalesce(e.department, '') as new_department,
--     r.mine_section as old_section, coalesce(e.section, '') as new_section
--   from ppe_records r
--   join reg e on upper(trim(r.employee_id)) = upper(trim(e.employee_id)) and e.rn = 1
--   where trim(coalesce(r.employee_id, '')) <> ''
--     and (coalesce(trim(r.employee_name), '') <> trim(regexp_replace(e.first_name || ' ' || e.last_name, E'\\s+', ' ', 'g'))
--       or coalesce(trim(r.position), '') <> coalesce(trim(e.designation), '')
--       or coalesce(trim(r.department), '') <> coalesce(trim(e.department), '')
--       or coalesce(trim(r.mine_section), '') <> coalesce(trim(e.section), ''))
--   order by r.id;
--
-- Safety: one transaction; every changed row is first copied to ppe_records_backup_2026_10_21; a guard aborts unless
-- exactly 359 rows match (if the preview count moved, update the guard after checking why); only numbers on the
-- register are touched (the 2 unmatched people keep their stored copies until re-linked in the app). Writes made here
-- bypass the app, so they are not in the audit log. Re-running fails loudly (the backup table already exists).
-- Rollback: update ppe_records r set employee_name = b.employee_name, position = b.position,
--   department = b.department, mine_section = b.mine_section, updated_at = now()
--   from ppe_records_backup_2026_10_21 b where b.id = r.id;

begin;
create temp table _ppe_fix on commit drop as
with reg as (
  select employee_id, first_name, last_name, designation, department, section,
    row_number() over (partition by upper(trim(employee_id)) order by id) as rn
  from employees
  where trim(coalesce(employee_id, '')) <> ''
)
select r.id as r_id,
  trim(regexp_replace(e.first_name || ' ' || e.last_name, E'\\s+', ' ', 'g')) as new_name,
  coalesce(e.designation, '') as new_position,
  coalesce(e.department, '') as new_department,
  coalesce(e.section, '') as new_section
from ppe_records r
join reg e on upper(trim(r.employee_id)) = upper(trim(e.employee_id)) and e.rn = 1
where trim(coalesce(r.employee_id, '')) <> ''
  and (coalesce(trim(r.employee_name), '') <> trim(regexp_replace(e.first_name || ' ' || e.last_name, E'\\s+', ' ', 'g'))
    or coalesce(trim(r.position), '') <> coalesce(trim(e.designation), '')
    or coalesce(trim(r.department), '') <> coalesce(trim(e.department), '')
    or coalesce(trim(r.mine_section), '') <> coalesce(trim(e.section), ''));

do $$
declare n int;
begin
  select count(*) into n from _ppe_fix;
  if n <> 359 then
    raise exception 'Expected 359 rows to align, found %. Nothing was changed (re-run the preview; the data moved).', n;
  end if;
end $$;

create table ppe_records_backup_2026_10_21 as
select r.* from ppe_records r join _ppe_fix f on f.r_id = r.id;

update ppe_records r set
  employee_name = f.new_name, position = f.new_position, department = f.new_department, mine_section = f.new_section,
  updated_at = now()
from _ppe_fix f
where r.id = f.r_id;
commit;
