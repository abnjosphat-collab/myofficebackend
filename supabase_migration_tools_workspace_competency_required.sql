begin;

-- Simple items (a tape measure, a hammer) need no training, qualification or
-- authorization round. Default true keeps every existing tool exactly as strict
-- as it is today; only tools explicitly marked open skip the competency gate.
alter table public.tools_workspace_equipment
  add column if not exists competency_required boolean not null default true;

commit;
