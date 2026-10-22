-- Goodwill Ncube (TBA03, id 38840): designation Supervisor -> Draughtsman
-- (owner instruction 22 Oct 2026). 'Draughtsman' joined the designation
-- catalogue (frontend/lib/employeeCatalog.ts, section Planning) the same day.
-- Section stays Planning, department untouched. Guarded to the single row.
-- RAN 2026-10-22 via scripts/apply_migration.py, then recorded in the ledger.
begin;
do $$
declare n int;
begin
  select count(*) into n from public.employees where id = 38840 and designation = 'Supervisor';
  if n <> 1 then
    raise exception 'Expected Goodwill Ncube (id 38840) to still be Supervisor, found % matching rows. Nothing was changed.', n;
  end if;
end $$;
update public.employees set designation = 'Draughtsman', updated_at = now()
where id = 38840 and designation = 'Supervisor';
commit;
-- Verify: select id, employee_id, first_name, last_name, designation, section
-- from public.employees where id = 38840;  -- one row, Draughtsman / Planning
