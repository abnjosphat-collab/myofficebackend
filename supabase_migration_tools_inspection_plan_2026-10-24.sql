-- Tools Part 3: the quarterly inspection plan table. NOT RUN - written for the owner to review and run.
--
-- One row per tool per quarter (quarter key 'YYYY-QN'): the planned date within the quarter's first 10 working
-- days and the inspection that completed it (null while pending). The backend generates the plan on first read
-- of the quarter and backfills tools that arrive later; until this file has run, quarterly checks fall back to
-- next-due dates, so the app keeps working either way. Rerunnable (if not exists throughout).

begin;
create table if not exists public.tools_workspace_inspection_plan (id uuid primary key default gen_random_uuid(),tool_id uuid not null references public.tools_workspace_equipment(id) on delete cascade,quarter text not null,planned_date date not null,completed_inspection_id uuid references public.tools_workspace_inspections(id) on delete set null,created_at timestamptz not null default now(),unique(tool_id,quarter));
alter table public.tools_workspace_inspection_plan enable row level security;
create index if not exists tools_workspace_inspection_plan_quarter_idx on public.tools_workspace_inspection_plan(quarter,planned_date);
create index if not exists tools_workspace_inspection_plan_tool_idx on public.tools_workspace_inspection_plan(tool_id,quarter);
commit;
