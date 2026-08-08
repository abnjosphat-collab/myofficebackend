-- supabase_migration_lookup_lists.sql
-- Run this in the Supabase SQL editor before deploying the backend changes that
-- depend on it (backend/app/routers/lookup_lists.py, and breakdowns.py's
-- auto-learn upsert).
--
-- One generic table serves every "pick from a growing list, or type a new value
-- and have it remembered" field across the app (list_name + value), rather than a
-- bespoke table per field. Today's consumers: breakdown_location, breakdown_nature.

create table if not exists lookup_lists (
  id bigint generated always as identity primary key,
  list_name text not null,
  value text not null,
  created_at timestamptz not null default now()
);

-- Case-insensitive dedup at the DB level (not just application-side) — "Bearing
-- Failure" and "bearing failure" are the same list entry.
create unique index if not exists idx_lookup_lists_name_value_ci
  on lookup_lists (list_name, lower(value));

create index if not exists idx_lookup_lists_name on lookup_lists (list_name);

-- New short, single-line "Nature of Breakdown" field — separate from the existing
-- multiline breakdown_description narrative (see the plan for why: the shared
-- pick-or-type UI component is a single-line input, not a textarea).
alter table breakdowns add column if not exists breakdown_nature text;
