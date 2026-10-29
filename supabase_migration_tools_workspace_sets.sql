-- Tool sets: a set head is an ordinary equipment row whose loan fans out to
-- member rows, so one issue/return moves the whole set (e.g. a 228-piece
-- Gedore set issued to one artisan). Membership is relational, not a quantity:
-- the register keeps one row per physical item and custody stays single-holder.
-- Safe to re-run (all objects IF NOT EXISTS; function bodies replaced wholesale).
begin;

create table if not exists public.tools_workspace_set_members (
  id uuid primary key default gen_random_uuid(),
  set_tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  member_tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  qty integer not null default 1 check (qty >= 1),
  created_by text not null,
  created_at timestamptz not null default now(),
  unique (member_tool_id)
);
create index if not exists tools_workspace_set_members_set_idx on public.tools_workspace_set_members(set_tool_id);

alter table public.tools_workspace_changes
  add column if not exists change_group_id uuid;
create index if not exists tools_workspace_changes_group_idx on public.tools_workspace_changes(change_group_id);

-- One transaction for a whole set movement: version-check every row first,
-- then apply all rows, history entries and change rows (sharing group_id).
create or replace function public.tools_workspace_apply_set_change(payload jsonb) returns jsonb language plpgsql security definer set search_path=public as $$
declare idem text; result_value jsonb; group_id uuid; item jsonb; current_version int; expected_version int; saved public.tools_workspace_equipment; event jsonb; change_id uuid; saved_tools jsonb := '[]'::jsonb; change_ids jsonb := '[]'::jsonb;
begin
 idem:=nullif(trim(payload->>'idempotency_key'),''); if idem is null then raise exception 'IDEMPOTENCY_REQUIRED' using errcode='P0001'; end if;
 select result into result_value from public.tools_workspace_idempotency where key=idem; if found then return jsonb_build_object('replayed',true,'result',result_value); end if;
 group_id:=coalesce(nullif(payload->>'group_id','')::uuid,gen_random_uuid());
 for item in select * from jsonb_array_elements(payload->'items') loop
  expected_version:=nullif(item->>'expected_version','')::int;
  select row_version into current_version from public.tools_workspace_equipment where id=(item->>'tool_id')::uuid for update;
  if current_version is null then raise exception 'TOOL_NOT_FOUND' using errcode='P0001'; end if;
  if expected_version is not null and current_version<>expected_version then raise exception 'STALE_VERSION' using errcode='P0001'; end if;
 end loop;
 for item in select * from jsonb_array_elements(payload->'items') loop
  insert into public.tools_workspace_equipment select * from jsonb_populate_record(null::public.tools_workspace_equipment,item->'after_state')
  on conflict(id) do update set register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,row_version=public.tools_workspace_equipment.row_version+1,updated_at=now() returning * into saved;
  event:=coalesce(item->'event','{}'::jsonb);
  insert into public.tools_workspace_history(id,tool_id,tool_name,action,detail,actor_name,employee_id,employee_name,event_at,metadata) values(coalesce(nullif(event->>'id','')::uuid,gen_random_uuid()),saved.id,saved.name,item->>'action',event->>'detail',payload->>'actor_name',nullif(event->>'employee_id','')::uuid,event->>'employee_name',coalesce(nullif(event->>'event_at','')::timestamptz,now()),coalesce(event->'metadata','{}'::jsonb));
  change_id:=coalesce(nullif(item->>'change_id','')::uuid,gen_random_uuid());
  insert into public.tools_workspace_changes(id,tool_id,action,before_state,after_state,actor_name,change_group_id) values(change_id,saved.id,item->>'action',coalesce(item->'before_state','{}'::jsonb),to_jsonb(saved),payload->>'actor_name',group_id);
  saved_tools:=saved_tools || jsonb_build_array(to_jsonb(saved)); change_ids:=change_ids || jsonb_build_array(change_id);
 end loop;
 result_value:=jsonb_build_object('tools',saved_tools,'change_ids',change_ids,'group_id',group_id); insert into public.tools_workspace_idempotency(key,result) values(idem,result_value);
 return jsonb_build_object('replayed',false,'result',result_value);
end $$;
revoke all on function public.tools_workspace_apply_set_change(jsonb) from public;
grant execute on function public.tools_workspace_apply_set_change(jsonb) to service_role;

-- Undo/redo a whole group at once (newest member first), so one undo re-opens
-- or closes the entire set loan. Falls back to the single-change function when
-- the change carries no group.
create or replace function public.tools_workspace_restore_change_group(group_id uuid,actor text,redo boolean default false)
returns jsonb language plpgsql security definer set search_path=public as $$
declare change_row public.tools_workspace_changes; desired jsonb; saved public.tools_workspace_equipment; saved_tools jsonb := '[]'::jsonb;
begin
 for change_row in select * from public.tools_workspace_changes where change_group_id=group_id order by created_at desc for update loop
  if redo and change_row.undone_at is null then raise exception 'CHANGE_NOT_UNDONE' using errcode='P0001'; end if;
  if not redo and change_row.undone_at is not null then raise exception 'CHANGE_ALREADY_UNDONE' using errcode='P0001'; end if;
  perform 1 from public.tools_workspace_equipment where id=change_row.tool_id for update;
  desired:=case when redo then change_row.after_state else change_row.before_state end;
  insert into public.tools_workspace_equipment select * from jsonb_populate_record(null::public.tools_workspace_equipment,desired)
  on conflict(id) do update set register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,row_version=public.tools_workspace_equipment.row_version+1,updated_at=now() returning * into saved;
  update public.tools_workspace_changes set undone_at=case when redo then null else now() end,undone_by=case when redo then null else actor end where id=change_row.id;
  insert into public.tools_workspace_history(tool_id,tool_name,action,detail,actor_name,metadata) values(saved.id,saved.name,case when redo then 'redo' else 'undo' end,change_row.action,actor,jsonb_build_object('change_id',change_row.id,'change_group_id',group_id));
  saved_tools:=saved_tools || jsonb_build_array(to_jsonb(saved));
 end loop;
 return jsonb_build_object('tools',saved_tools,'group_id',group_id,'redo',redo);
end $$;
revoke all on function public.tools_workspace_restore_change_group(uuid,text,boolean) from public;
grant execute on function public.tools_workspace_restore_change_group(uuid,text,boolean) to service_role;

alter table public.tools_workspace_set_members enable row level security;

commit;
