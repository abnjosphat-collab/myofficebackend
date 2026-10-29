-- Monthly toolbox checks: set checklists that mix register members with loose
-- content lines (name/size/qty, never individually tracked), plus one submitted
-- check per set per calendar month with per-item states, reasons and narratives.
-- Safe to re-run (all objects IF NOT EXISTS).
begin;

-- Loose contents of a set head: "spanner 14mm x2". Register members stay in
-- tools_workspace_set_members; both appear on the monthly checklist.
create table if not exists public.tools_workspace_set_lines (
  id uuid primary key default gen_random_uuid(),
  set_tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  name text not null,
  size text,
  qty integer not null default 1 check (qty >= 1),
  notes text,
  created_by text not null,
  created_at timestamptz not null default now()
);
create index if not exists tools_workspace_set_lines_set_idx on public.tools_workspace_set_lines(set_tool_id);

-- One header row per set per calendar month (period 'YYYY-MM').
create table if not exists public.tools_workspace_toolbox_checks (
  id uuid primary key default gen_random_uuid(),
  set_tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  period char(7) not null check (period ~ '^[0-9]{4}-[0-9]{2}$'),
  holder_employee_id uuid,
  holder_name text not null,
  status text not null default 'open' check (status in ('open','submitted')),
  checked_by_account_id uuid,
  checked_by_name text,
  checked_at timestamptz,
  created_at timestamptz not null default now(),
  unique (set_tool_id, period)
);
create index if not exists tools_workspace_toolbox_checks_period_idx on public.tools_workspace_toolbox_checks(period);
create index if not exists tools_workspace_toolbox_checks_set_idx on public.tools_workspace_toolbox_checks(set_tool_id);

-- Submitted items snapshot the name/size/qty at submit time, so later line
-- edits never rewrite history. kind='member' rows reference the register tool;
-- kind='line' rows reference the loose content line.
create table if not exists public.tools_workspace_toolbox_check_items (
  id uuid primary key default gen_random_uuid(),
  check_id uuid not null references public.tools_workspace_toolbox_checks(id) on delete cascade,
  kind text not null check (kind in ('member','line')),
  member_tool_id uuid references public.tools_workspace_equipment(id) on delete restrict,
  line_id uuid references public.tools_workspace_set_lines(id) on delete restrict,
  name_snapshot text not null,
  size_snapshot text,
  qty_expected integer not null default 1 check (qty_expected >= 1),
  qty_found integer not null default 1 check (qty_found >= 0),
  state text not null check (state in ('available','missing','damaged','borrowed','stolen')),
  note text not null default '',
  how_happened text,
  prevention text,
  follow_up text,
  created_at timestamptz not null default now(),
  check (kind = 'member' and member_tool_id is not null and line_id is null or kind = 'line' and line_id is not null and member_tool_id is null)
);
create index if not exists tools_workspace_toolbox_check_items_check_idx on public.tools_workspace_toolbox_check_items(check_id);

alter table public.tools_workspace_set_lines enable row level security;
alter table public.tools_workspace_toolbox_checks enable row level security;
alter table public.tools_workspace_toolbox_check_items enable row level security;

commit;
