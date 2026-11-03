-- Remove the last 3 non-Engineering DEMO personnel (MTS-001..003) and their test history.
--
-- Owner decision 2026-11-03: after the first removal file deleted the 9 unreferenced
-- DEMO rows, these three Mine Technical Services rows remain only because six
-- 27 Sep 2026 test-issue history rows point at them (Tapiwa Zhou x3, Rutendo Gumbo
-- x2, Simba Chigariro x1; everything long returned, nothing outstanding). The owner
-- chose to clear that test history and delete the rows rather than keep them as
-- audit residue.
--
-- Safety, in order:
--   1. The three employee numbers are an explicit allowlist, not a pattern.
--   2. Every history row naming one of them must be from the 27 Sep 2026 test
--      session, otherwise the run aborts (real later use would be audit, not test).
--   3. None of the three may currently hold a tool or set (custody), be named in
--      an incident, or hold a monthly-check slot; any of that aborts the run.
--   4. Backups first, inside the same transaction: the history rows go to
--      tools_workspace_history_demo_backup_20261103, the employee rows (with
--      competencies) append to tools_workspace_employees_demo_backup_20261103.
-- Re-running after success deletes nothing further and still passes.
-- Run whole-file in the Supabase SQL editor, or:
--     .venv/Scripts/python.exe scripts/apply_migration.py supabase_remove_demo_mts_history_2026-11-03.sql

begin;

do $$
declare
  expected_numbers constant text[] := array['DEMO-MTS-001', 'DEMO-MTS-002', 'DEMO-MTS-003'];
  target_ids uuid[];
  live_history integer;
  non_test_history integer;
begin
  select coalesce(array_agg(e.id), '{}')
    into target_ids
    from public.tools_workspace_employees e
   where e.employee_number = any (expected_numbers);

  if cardinality(target_ids) = 0 then
    raise notice 'No DEMO-MTS-001..003 rows remain; nothing to do.';
    return;
  end if;

  -- Only the 27 Sep 2026 test session may reference these rows.
  select count(*)::integer into live_history
    from public.tools_workspace_history h
   where h.employee_id = any (target_ids);
  select count(*)::integer into non_test_history
    from public.tools_workspace_history h
   where h.employee_id = any (target_ids)
     and h.event_at::date <> date '2026-09-27';
  if non_test_history > 0 then
    raise exception 'DEMO MTS rows have history outside the 27 Sep 2026 test session; refusing to clear audit trail.';
  end if;

  -- Nothing outstanding and nothing else pointing at them.
  if exists (select 1 from public.tools_workspace_equipment q
              where nullif(q.custody ->> 'employee_id', '')::uuid = any (target_ids)) then
    raise exception 'A DEMO MTS row still holds a tool or set; receive it first.';
  end if;
  if exists (select 1 from public.tools_workspace_incidents i where i.employee_id = any (target_ids)) then
    raise exception 'A DEMO MTS row is named in an incident; keeping the audit trail.';
  end if;
  if exists (select 1 from public.tools_workspace_toolbox_checks c where c.holder_employee_id = any (target_ids)) then
    raise exception 'A DEMO MTS row holds a monthly set check.';
  end if;

  create table if not exists public.tools_workspace_history_demo_backup_20261103 (
    like public.tools_workspace_history including all,
    removed_at timestamptz not null default now()
  );
  alter table public.tools_workspace_history_demo_backup_20261103 enable row level security;

  insert into public.tools_workspace_history_demo_backup_20261103
  select h.*, now()
    from public.tools_workspace_history h
   where h.employee_id = any (target_ids)
     and not exists (select 1 from public.tools_workspace_history_demo_backup_20261103 b where b.id = h.id);

  insert into public.tools_workspace_employees_demo_backup_20261103
    (id, employee_number, name, department, job_title, supervisor_name, active,
     created_by, created_at, updated_at, competencies)
  select e.id, e.employee_number, e.name, e.department, e.job_title, e.supervisor_name,
         e.active, e.created_by, e.created_at, e.updated_at,
         coalesce((select jsonb_agg(to_jsonb(c)) from public.tools_workspace_competencies c where c.employee_id = e.id), '[]'::jsonb)
    from public.tools_workspace_employees e
   where e.id = any (target_ids)
     and not exists (select 1 from public.tools_workspace_employees_demo_backup_20261103 b where b.id = e.id);

  delete from public.tools_workspace_history h where h.employee_id = any (target_ids);
  delete from public.tools_workspace_employees e where e.id = any (target_ids);

  raise notice 'Cleared % test history rows and removed % DEMO MTS personnel; both backups hold the rows.',
    live_history, cardinality(target_ids);
end
$$;

commit;
