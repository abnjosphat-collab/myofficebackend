-- Tools Part 2: trade section per person. NOT RUN - written for the owner to review and run.
--
-- Step 0 adds a nullable section to the Tools employee register. Step 1 backfills it from the main
-- employees register by mine number (38 people match, all Engineering; every matched register row carries
-- a clean Electrical/Mechanical section). Step 2 fills the 4 remaining Engineering people from their job
-- titles (the inference list the owner confirmed); the 12 non-Engineering demo rows have no inferable
-- trade and stay NULL. Section stays editable in the Tools employee form afterwards.
--
-- Guards: the file changes nothing unless the join is unambiguous (no mine number matches two register
-- rows) and exactly 38 + 4 rows are filled. Every touched row is first copied to
-- tools_workspace_employees_backup_2026_10_24. Writes made here bypass the app, so they are not in the
-- audit log. Rerunning after a successful apply raises (0 rows left to fill) instead of changing anything.

-- STEP 0: the column (safe to rerun on its own).
alter table tools_workspace_employees add column if not exists section text;

-- STEP 1: backup, then backfill by mine number.
begin;
create table if not exists tools_workspace_employees_backup_2026_10_24 (like tools_workspace_employees including all);
alter table tools_workspace_employees_backup_2026_10_24 enable row level security;

do $$
declare ambiguous int; matched int;
begin
  select count(*) into ambiguous
  from (select lower(trim(t.employee_number)) as number
        from tools_workspace_employees t
        where nullif(trim(coalesce(t.employee_number, '')), '') is not null
        group by lower(trim(t.employee_number))) t
  join (select lower(trim(e.employee_id)) as number
        from employees e
        where nullif(trim(coalesce(e.employee_id, '')), '') is not null
        group by lower(trim(e.employee_id))
        having count(*) > 1) e on e.number = t.number;
  if ambiguous <> 0 then
    raise exception 'Found % mine numbers matching two or more register rows. Nothing was changed.', ambiguous;
  end if;
  select count(*) into matched
  from tools_workspace_employees t
  join employees e on lower(trim(e.employee_id)) = lower(trim(t.employee_number))
  where nullif(trim(coalesce(t.employee_number, '')), '') is not null
    and nullif(trim(coalesce(e.section, '')), '') is not null
    and t.section is null;
  if matched <> 38 then
    raise exception 'Expected 38 people to backfill by mine number, found %. Nothing was changed.', matched;
  end if;
end $$;

insert into tools_workspace_employees_backup_2026_10_24
select t.* from tools_workspace_employees t
join employees e on lower(trim(e.employee_id)) = lower(trim(t.employee_number))
where nullif(trim(coalesce(t.employee_number, '')), '') is not null
  and nullif(trim(coalesce(e.section, '')), '') is not null
  and t.section is null
  and not exists (select 1 from tools_workspace_employees_backup_2026_10_24 b where b.id = t.id);

update tools_workspace_employees t set section = trim(e.section)
from employees e
where lower(trim(e.employee_id)) = lower(trim(t.employee_number))
  and nullif(trim(coalesce(t.employee_number, '')), '') is not null
  and nullif(trim(coalesce(e.section, '')), '') is not null
  and t.section is null
  and exists (select 1 from tools_workspace_employees_backup_2026_10_24 b where b.id = t.id);
commit;

-- STEP 2: the 4 owner-confirmed title inferences (run only after the owner confirms the list below).
--   DEMO-ENG-001 Tariro Moyo (Mechanical Fitter) -> Mechanical
--   DEMO-ENG-002 Tawanda Ncube (Electrician) -> Electrical
--   DEMO-ENG-003 Ruvimbo Dube (Boilermaker) -> Mechanical
--   DEMO-ENG-004 Farai Sibanda (Instrumentation Technician) -> Electrical
begin;
create temp table _confirmed (number text, section text) on commit drop;
insert into _confirmed values
  ('DEMO-ENG-001', 'Mechanical'),
  ('DEMO-ENG-002', 'Electrical'),
  ('DEMO-ENG-003', 'Mechanical'),
  ('DEMO-ENG-004', 'Electrical');

do $$
declare ready int;
begin
  select count(*) into ready
  from _confirmed c join tools_workspace_employees t on lower(trim(t.employee_number)) = lower(c.number)
  where t.section is null;
  if ready <> 4 then
    raise exception 'Expected the 4 confirmed people still without a section, found %. Nothing was changed.', ready;
  end if;
end $$;

insert into tools_workspace_employees_backup_2026_10_24
select t.* from tools_workspace_employees t
join _confirmed c on lower(trim(t.employee_number)) = lower(c.number)
where t.section is null
  and not exists (select 1 from tools_workspace_employees_backup_2026_10_24 b where b.id = t.id);

update tools_workspace_employees t set section = c.section
from _confirmed c
where lower(trim(t.employee_number)) = lower(c.number)
  and t.section is null
  and exists (select 1 from tools_workspace_employees_backup_2026_10_24 b where b.id = t.id);
commit;
