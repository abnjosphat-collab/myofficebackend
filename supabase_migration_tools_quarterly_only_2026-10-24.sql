-- Tools Part 3: quarterly-only inspections (owner decision). NOT RUN - written for the owner to review and run.
--
-- Sets monthly_inspection_required = false on every tool that still requires it (101 of 103 tools; 2 already
-- false). Weekly, calibration and pre-use requirements are untouched, and past monthly inspection records stay.
-- New tools default to no monthly inspection in the backend model and the tool form from this part on.
--
-- Guards: the file changes nothing unless exactly 101 tools still require monthly inspection. Every touched
-- row is first copied to tools_workspace_equipment_backup_2026_10_24 (shared with the Part 2 trade-sections
-- file; rows already backed up are not duplicated). Writes made here bypass the app, so they are not in the
-- audit log. Rerunning after a successful apply raises instead of changing anything.

begin;
create table if not exists tools_workspace_equipment_backup_2026_10_24 (like tools_workspace_equipment including all);
alter table tools_workspace_equipment_backup_2026_10_24 enable row level security;

do $$
declare ready int;
begin
  select count(*) into ready from tools_workspace_equipment where coalesce(monthly_inspection_required, true);
  if ready <> 101 then
    raise exception 'Expected 101 tools still requiring monthly inspection, found %. Nothing was changed.', ready;
  end if;
end $$;

insert into tools_workspace_equipment_backup_2026_10_24
select t.* from tools_workspace_equipment t
where coalesce(t.monthly_inspection_required, true)
  and not exists (select 1 from tools_workspace_equipment_backup_2026_10_24 b where b.id = t.id);

update tools_workspace_equipment t set monthly_inspection_required = false
where coalesce(t.monthly_inspection_required, true)
  and exists (select 1 from tools_workspace_equipment_backup_2026_10_24 b where b.id = t.id);
commit;
