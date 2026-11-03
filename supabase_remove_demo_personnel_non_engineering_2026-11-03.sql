-- Remove the DEMO example personnel outside Engineering from the Tools register.
--
-- Context: backend/scripts/seed_tools_personnel.py added 16 clearly-marked example
-- personnel (DEMO-ENG-001..004, DEMO-MIN-001..004, DEMO-MTS-001..004,
-- DEMO-IT-001..004). The four Engineering rows stay; the twelve rows for Mining,
-- Mine Technical Services and IT go now, ahead of their proper registers.
--
-- Safety, in order:
--   1. The twelve employee numbers are an explicit allowlist, not a pattern.
--   2. Any other DEMO- number on the register aborts the run (someone may have
--      added a real row under a DEMO- number since).
--   3. A row referenced by a live record is NOT deleted, only reported: an open
--      loan (equipment custody), issue history, an incident, or a monthly-check
--      holder slot. History and incidents are the audit trail and must keep their
--      employee link, so those rows stay until the owner decides otherwise.
--      Competency rows cascade with their employee (Postgres ON DELETE CASCADE)
--      and are counted in the notice below.
--   4. Every deleted row is copied to tools_workspace_employees_demo_backup_20261103
--      first, with its competencies, so the removal is reversible by number.
-- Re-running after success deletes nothing further and still passes.
-- Run whole-file in the Supabase SQL editor, or:
--     .venv/Scripts/python.exe scripts/apply_migration.py supabase_remove_demo_personnel_non_engineering_2026-11-03.sql

begin;

do $$
declare
  expected_numbers constant text[] := array[
    'DEMO-MIN-001', 'DEMO-MIN-002', 'DEMO-MIN-003', 'DEMO-MIN-004',
    'DEMO-MTS-001', 'DEMO-MTS-002', 'DEMO-MTS-003', 'DEMO-MTS-004',
    'DEMO-IT-001', 'DEMO-IT-002', 'DEMO-IT-003', 'DEMO-IT-004'
  ];
  target_ids uuid[];
  blocked_ids uuid[];
  unknown_demo text[];
  skipped text[];
  competency_count integer;
  removed_count integer;
begin
  -- Unknown DEMO rows: anything shaped like a demo number that is not one of the
  -- sixteen seeded ones (twelve removed here, four Engineering kept on purpose).
  select coalesce(array_agg(e.employee_number order by e.employee_number), '{}')
    into unknown_demo
    from public.tools_workspace_employees e
   where e.employee_number like 'DEMO-%'
     and e.employee_number <> all (
       expected_numbers || array['DEMO-ENG-001', 'DEMO-ENG-002', 'DEMO-ENG-003', 'DEMO-ENG-004']
     );
  if cardinality(unknown_demo) > 0 then
    raise exception 'Unexpected DEMO rows on the register (not part of the 2026-11-03 removal): %', array_to_string(unknown_demo, ', ');
  end if;

  select coalesce(array_agg(e.id), '{}')
    into target_ids
    from public.tools_workspace_employees e
   where e.employee_number = any (expected_numbers);

  -- Rows a live record still points at stay put and are named in the notice.
  select coalesce(array_agg(distinct e.id), '{}')
    into blocked_ids
    from public.tools_workspace_employees e
   where e.id = any (target_ids)
     and (
       exists (select 1 from public.tools_workspace_equipment q where nullif(q.custody ->> 'employee_id', '')::uuid = e.id)
       or exists (select 1 from public.tools_workspace_history h where h.employee_id = e.id)
       or exists (select 1 from public.tools_workspace_incidents i where i.employee_id = e.id)
       or exists (select 1 from public.tools_workspace_toolbox_checks c where c.holder_employee_id = e.id)
     );

  select coalesce(array_agg(e.employee_number order by e.employee_number), '{}')
    into skipped
    from public.tools_workspace_employees e
   where e.id = any (blocked_ids);

  select count(*)::integer into competency_count
    from public.tools_workspace_competencies c
   where c.employee_id = any (target_ids)
     and not (c.employee_id = any (blocked_ids));

  create table if not exists public.tools_workspace_employees_demo_backup_20261103 (
    like public.tools_workspace_employees including all,
    competencies jsonb not null default '[]'::jsonb,
    removed_at timestamptz not null default now()
  );
  alter table public.tools_workspace_employees_demo_backup_20261103 enable row level security;

  insert into public.tools_workspace_employees_demo_backup_20261103
    (id, employee_number, name, department, job_title, supervisor_name, active,
     created_by, created_at, updated_at, competencies)
  select e.id, e.employee_number, e.name, e.department, e.job_title, e.supervisor_name,
         e.active, e.created_by, e.created_at, e.updated_at,
         coalesce((select jsonb_agg(to_jsonb(c)) from public.tools_workspace_competencies c where c.employee_id = e.id), '[]'::jsonb)
    from public.tools_workspace_employees e
   where e.id = any (target_ids)
     and not (e.id = any (blocked_ids))
     and not exists (select 1 from public.tools_workspace_employees_demo_backup_20261103 b where b.id = e.id);

  delete from public.tools_workspace_employees e
   where e.id = any (target_ids)
     and not (e.id = any (blocked_ids));
  get diagnostics removed_count = row_count;

  if cardinality(skipped) > 0 then
    raise notice 'Kept % referenced demo row(s), still on the register: %. Delete them only once their history is intentionally cleared.',
      cardinality(skipped), array_to_string(skipped, ', ');
  end if;
  raise notice 'Removed % demo personnel outside Engineering; % cascading competency rows go with them; backup holds % rows.',
    removed_count, competency_count,
    (select count(*) from public.tools_workspace_employees_demo_backup_20261103);
end
$$;

commit;
