-- Mining account + Excel importer (Batch 4, 30 Oct 2026).
--
-- What this does:
--   1. New table employee_imports: one row per Excel import (who, when, file
--      name, counts, the employee and PPE ids it created, and when/if undone).
--      Empty today; the backup below owns it from the start.
--   2. New function get_user_limits_by_id(UUID): the scope sibling that also
--      carries modules — returns {"scope": <raw permissions.scope JSON or
--      null>, "modules": <permissions.modules or null>}. SECURITY DEFINER +
--      STABLE so the backend's anon key can call it, same as the role/scope
--      lookups. The backend tries this first and falls back to the older
--      get_user_scope_by_id, so a deploy that lands before this file still
--      signs in (without modules) instead of failing.
--
-- It touches NO user row: no permissions are created, changed or cleared
-- here — the owner sets the Mining user in Admin after this lands.
--
-- Undo: before any import is committed through the API,
--     drop function public.get_user_limits_by_id(UUID);
--     drop table public.employee_imports;
--     drop table if exists public.employee_imports_backup_2026_10_30;
-- After an import is committed, that import's row (and the people/records it
-- created — undoable from the import's log in Employees) must be kept: export
-- the rows first (select * from public.employee_imports;).
--
-- Re-runnable: every object uses IF NOT EXISTS / CREATE OR REPLACE.
begin;

-- Dry run: what will change (RAISE NOTICE output, no writes).
do $$
declare import_count int;
begin
  select count(*) into import_count from public.employee_imports;
  raise notice 'DRY RUN mining migration: employee_imports rows=% (new table, starts empty)', import_count;
exception when undefined_table then
  raise notice 'DRY RUN: employee_imports does not exist yet; will be created empty.';
end $$;

-- 1. Import log.
create table if not exists public.employee_imports (
  id uuid primary key default gen_random_uuid(),
  department text not null,
  file_name text not null,
  created_by text,
  created_at timestamptz not null default now(),
  counts jsonb not null default '{}'::jsonb,
  created_employee_ids jsonb not null default '[]'::jsonb,
  created_ppe_ids jsonb not null default '[]'::jsonb,
  undone_at timestamptz
);

-- Backup (empty today, but the file owns it from the start).
create table if not exists public.employee_imports_backup_2026_10_30 as
  select * from public.employee_imports where false;

-- Guard: refuse if rows exist that an import did not write through the API
-- (imports only ever insert; an existing row means someone hand-wrote one).
do $$
declare bad int;
begin
  select count(*) into bad from public.employee_imports where created_by is null and counts = '{}'::jsonb;
  if bad > 0 then
    raise exception 'GUARD: employee_imports holds % hand-written row(s); read them before re-running.', bad;
  end if;
end $$;

-- 2. Limits lookup: scope (raw JSON, string or {"kind","value"}) + modules.
create or replace function public.get_user_limits_by_id(user_id UUID)
returns JSONB
language SQL
security definer
stable as $$
  select jsonb_build_object(
    'scope', permissions->'scope',
    'modules', coalesce(permissions->'modules', 'null'::jsonb)
  ) from public.user_profiles where id = user_id;
$$;

-- Guardrail: the function answers nulls for a nobody — never an error, never
-- another account's limits.
do $$
declare probe JSONB;
begin
  select public.get_user_limits_by_id('00000000-0000-0000-0000-000000000000') into probe;
  if probe is not null then
    raise exception 'get_user_limits_by_id leaked for a nobody — refusing to continue';
  end if;
end $$;

-- 3. RLS (service_role only, as with the other account tables).
alter table public.employee_imports enable row level security;

commit;
