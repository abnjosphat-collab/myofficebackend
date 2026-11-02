-- PPE size requirements (Batch 4, 2 Nov 2026).
--
-- What this does:
--   New table public.ppe_requirements: planned size allocations (e.g. the
--   Engineering raincoat register) linked to employees by mine number. These
--   are requirements, never issues: fulfilment is derived at read time from
--   active ppe_records, so importing requirements cannot fabricate issue
--   records or issue dates.
--   Unique on (source_ref, ppe_type, source_name): re-running an import
--   skips existing rows instead of duplicating them.
--   RLS enabled (service_role only, same pattern as ppe_events); the API
--   reaches the table through the authenticated endpoint.
--
-- Undo: the table holds no live rows yet (guard below proves it on first run).
--   To roll back before any import:
--     drop table public.ppe_requirements;
--     drop table if exists public.ppe_requirements_backup_2026_11_02;
--   After requirements are imported, rolling back loses them: export first
--   (their rows live in ppe_requirements).
--
-- Re-runnable: every object uses IF NOT EXISTS / CREATE OR REPLACE, and the
-- unique key refuses duplicates on re-import.
begin;

-- Dry run: what will change (RAISE NOTICE output, no writes).
do $$
declare
  requirement_count int;
begin
  select count(*) into requirement_count from public.ppe_requirements;
  raise notice 'DRY RUN ppe-requirements migration: ppe_requirements=%', requirement_count;
exception when undefined_table then
  raise notice 'DRY RUN: ppe_requirements does not exist yet; will create it with RLS.';
end $$;

-- Backup (empty today, but the file owns it from the start).
create table if not exists public.ppe_requirements_backup_2026_11_02 as
  select * from public.ppe_requirements where false;

-- Requirements register.
create table if not exists public.ppe_requirements (
  id uuid primary key default gen_random_uuid(),
  employee_id text,
  source_name text not null,
  employee_name text,
  department text,
  mine_section text,
  ppe_type text not null default 'rainsuit',
  size_source text,
  size_canonical text,
  quantity integer not null default 1 check (quantity >= 1),
  status text not null default 'required' check (status in ('required')),
  source_ref text not null,
  notes text,
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  row_version integer not null default 1
);
create index if not exists ppe_requirements_employee_idx on public.ppe_requirements(employee_id);
create index if not exists ppe_requirements_department_idx on public.ppe_requirements(department);

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'ppe_requirements_source_unique'
  ) then
    alter table public.ppe_requirements
      add constraint ppe_requirements_source_unique
      unique (source_ref, ppe_type, source_name);
  end if;
end $$;

alter table public.ppe_requirements enable row level security;

commit;
