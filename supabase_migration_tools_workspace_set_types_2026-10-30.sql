-- Set types and numbered pieces (Batch 2, 30 Oct 2026).
--
-- What this does:
--   1. New tables tools_workspace_set_types and tools_workspace_set_type_lines
--      (the catalogue: enter a set's contents once, then make physical sets from it).
--   2. tools_workspace_equipment gains nullable set_type_id (a physical set stays
--      an ordinary equipment row, so custody/issue/return/history/undo/inspections
--      and the monthly toolbox checks keep working).
--   3. tools_workspace_set_lines (the pieces of one physical set) gains line_no,
--      piece_number (unique, e.g. SET-03/017), condition, status, set_type_line_id
--      and updated_at.
--   4. RLS enabled on the new tables (service_role only, same pattern as the
--      other Tools tables; the API reaches them through security-definer RPCs).
--
-- Undo: the new tables hold no live rows yet (guard below proves it on first run).
--   To roll back before any set is entered:
--     drop table public.tools_workspace_set_type_lines;
--     drop table public.tools_workspace_set_types;
--     alter table public.tools_workspace_set_lines drop column if exists set_type_line_id,
--       drop column if exists status, drop column if exists condition,
--       drop column if exists piece_number, drop column if exists line_no,
--       drop column if exists updated_at;
--     alter table public.tools_workspace_equipment drop column if exists set_type_id;
--     drop table if exists public.tools_workspace_set_lines_backup_2026_10_30;
--     drop table if exists public.tools_workspace_equipment_backup_2026_10_30;
--   After sets are entered, rolling back loses those sets: export them first
--   (their rows live in tools_workspace_equipment + tools_workspace_set_lines).
--
-- Re-runnable: every object uses IF NOT EXISTS / CREATE OR REPLACE, and the
-- guard refuses only when pieces exist that this migration cannot number.
begin;

-- Dry run: what will change (RAISE NOTICE output, no writes).
do $$
declare
  type_count int; line_count int; piece_count int; equipment_count int;
begin
  select count(*) into type_count from public.tools_workspace_set_types;
  select count(*) into line_count from public.tools_workspace_set_type_lines;
  select count(*) into piece_count from public.tools_workspace_set_lines;
  select count(*) into equipment_count from public.tools_workspace_equipment;
  raise notice 'DRY RUN set-types migration: set_types=% type_lines=% set_lines(pieces)=% equipment=%', type_count, line_count, piece_count, equipment_count;
  raise notice 'DRY RUN: will create set_types + set_type_lines, add set_type_id to equipment, add line_no/piece_number/condition/status/set_type_line_id/updated_at to set_lines.';
exception when undefined_table then
  raise notice 'DRY RUN: set type tables do not exist yet; set_lines/equipment counts skipped.';
end $$;

-- Backups (empty today, but the file owns them from the start).
create table if not exists public.tools_workspace_set_lines_backup_2026_10_30 as
  select * from public.tools_workspace_set_lines where false;
create table if not exists public.tools_workspace_equipment_backup_2026_10_30 as
  select * from public.tools_workspace_equipment where false;

-- 1. Set-type catalogue.
create table if not exists public.tools_workspace_set_types (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  make_model text,
  category text,
  competency_required boolean not null default false,
  notes text,
  active boolean not null default true,
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  row_version integer not null default 1
);

create table if not exists public.tools_workspace_set_type_lines (
  id uuid primary key default gen_random_uuid(),
  set_type_id uuid not null references public.tools_workspace_set_types(id) on delete restrict,
  line_no int not null,
  name text not null,
  size text,
  qty int not null default 1 check (qty >= 1),
  notes text,
  created_at timestamptz not null default now(),
  unique (set_type_id, line_no)
);
create index if not exists tools_workspace_set_type_lines_type_idx on public.tools_workspace_set_type_lines(set_type_id);

-- 2. Physical sets point at their type.
alter table public.tools_workspace_equipment
  add column if not exists set_type_id uuid;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'tools_workspace_equipment_set_type_fk'
  ) then
    alter table public.tools_workspace_equipment
      add constraint tools_workspace_equipment_set_type_fk
      foreign key (set_type_id) references public.tools_workspace_set_types(id) on delete restrict;
  end if;
end $$;

-- 3. Pieces of one physical set.
alter table public.tools_workspace_set_lines
  add column if not exists line_no int;
alter table public.tools_workspace_set_lines
  add column if not exists piece_number text;
alter table public.tools_workspace_set_lines
  add column if not exists condition text not null default 'Good';
alter table public.tools_workspace_set_lines
  add column if not exists status text not null default 'present';
alter table public.tools_workspace_set_lines
  add column if not exists set_type_line_id uuid;
alter table public.tools_workspace_set_lines
  add column if not exists updated_at timestamptz not null default now();

-- Guard: refuse when pieces exist that this migration cannot number.
do $$
declare bad int;
begin
  select count(*) into bad from public.tools_workspace_set_lines where line_no is null;
  if bad > 0 then
    raise exception 'GUARD: % set_lines rows have no line_no; number them before re-running.', bad;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'tools_workspace_set_lines_piece_number_unique'
  ) then
    alter table public.tools_workspace_set_lines
      add constraint tools_workspace_set_lines_piece_number_unique unique (piece_number);
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'tools_workspace_set_lines_status_check'
  ) then
    alter table public.tools_workspace_set_lines
      add constraint tools_workspace_set_lines_status_check
      check (status in ('present', 'missing', 'damaged'));
  end if;
  if not exists (
    select 1 from pg_constraint where conname = 'tools_workspace_set_lines_type_line_fk'
  ) then
    alter table public.tools_workspace_set_lines
      add constraint tools_workspace_set_lines_type_line_fk
      foreign key (set_type_line_id) references public.tools_workspace_set_type_lines(id) on delete restrict;
  end if;
end $$;

create index if not exists tools_workspace_set_lines_piece_idx on public.tools_workspace_set_lines(piece_number);

-- 4. RLS on the new tables (service_role only, as with the other Tools tables).
alter table public.tools_workspace_set_types enable row level security;
alter table public.tools_workspace_set_type_lines enable row level security;

commit;
