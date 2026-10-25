-- Tools P1: the quarterly plan starts in January. Backs up and removes the 2026-Q4 plan rows (62 rows on October
-- dates, none completed, as checked 25 Oct 2026); the 2027-Q1 row stays. RUN ONLY AFTER the P1 release is live:
-- before the release, opening Tools regenerates 2026-Q4 rows; after it, the empty quarter stays empty by design and
-- quarterly checks stop showing as due until the January plan. Rerunnable: a second run backs up and removes nothing new.
--
-- Guards: refuses unless every remaining 2026-Q4 row has an October planned_date; the delete and the backup must agree.
begin;
create table if not exists public.tools_workspace_inspection_plan_q4_2026_backup (id uuid primary key,tool_id uuid not null,quarter text not null,planned_date date not null,completed_inspection_id uuid,created_at timestamptz not null);
alter table public.tools_workspace_inspection_plan_q4_2026_backup enable row level security;
do $$
declare
  non_october int;
  backed_up int;
  removed int;
begin
  select count(*) into non_october from public.tools_workspace_inspection_plan
  where quarter = '2026-Q4' and (planned_date < date '2026-10-01' or planned_date >= date '2026-11-01');
  if non_october > 0 then
    raise exception 'Expected only October dates on 2026-Q4 plan rows, found % outside October. Nothing was changed.', non_october;
  end if;
  insert into public.tools_workspace_inspection_plan_q4_2026_backup select * from public.tools_workspace_inspection_plan p
  where p.quarter = '2026-Q4' and not exists (select 1 from public.tools_workspace_inspection_plan_q4_2026_backup b where b.id = p.id);
  get diagnostics backed_up = row_count;
  delete from public.tools_workspace_inspection_plan where quarter = '2026-Q4';
  get diagnostics removed = row_count;
  if backed_up != removed then
    raise exception 'Backup (%) and delete (%) disagree. Nothing was changed.', backed_up, removed;
  end if;
  raise notice 'Backed up % rows, removed % 2026-Q4 plan rows.', backed_up, removed;
end $$;
commit;
