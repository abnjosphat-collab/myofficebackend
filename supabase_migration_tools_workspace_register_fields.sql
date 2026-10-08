-- Tools & equipment register: the fields the Engineering register records that the workspace did not yet hold
-- (purchase date, procurement cost, insured, last verified, working status). Purely additive: every column is nullable, no
-- existing row changes, and nothing is dropped. The two change functions are re-created so the new columns are saved and undone
-- with the rest of a record.
begin;

alter table public.tools_workspace_equipment
  add column if not exists purchase_date date,
  add column if not exists procurement_cost numeric(14,2),
  add column if not exists insured boolean,
  add column if not exists last_verified text,
  add column if not exists working_status text check(working_status is null or working_status in ('working','not_working'));

create or replace function public.tools_workspace_apply_change(payload jsonb) returns jsonb language plpgsql security definer set search_path=public as $$
declare idem text; result_value jsonb; current_version int; expected_version int; saved public.tools_workspace_equipment; event jsonb; change_id uuid;
begin
  idem:=nullif(trim(payload->>'idempotency_key'),''); if idem is null then raise exception 'IDEMPOTENCY_REQUIRED' using errcode='P0001'; end if;
  select result into result_value from public.tools_workspace_idempotency where key=idem; if found then return jsonb_build_object('replayed',true,'result',result_value); end if;
  expected_version:=nullif(payload->>'expected_version','')::int;
  if payload->>'tool_id' is not null then
    select row_version into current_version from public.tools_workspace_equipment where id=(payload->>'tool_id')::uuid for update;
    if current_version is null then raise exception 'TOOL_NOT_FOUND' using errcode='P0001'; end if;
    if expected_version is not null and current_version<>expected_version then raise exception 'STALE_VERSION' using errcode='P0001'; end if;
  end if;
  insert into public.tools_workspace_equipment select * from jsonb_populate_record(null::public.tools_workspace_equipment,payload->'after_state')
  on conflict(id) do update set
    register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,
    category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,
    section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,
    calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,
    site_code=excluded.site_code,area_code=excluded.area_code,department_code=excluded.department_code,activity_code=excluded.activity_code,
    home_storage_location=excluded.home_storage_location,storage_conditions=excluded.storage_conditions,
    maintenance_requirements=excluded.maintenance_requirements,pre_use_check_required=excluded.pre_use_check_required,
    weekly_inspection_required=excluded.weekly_inspection_required,monthly_inspection_required=excluded.monthly_inspection_required,
    quarterly_inspection_required=excluded.quarterly_inspection_required,calibration_required=excluded.calibration_required,
    calibration_frequency_days=excluded.calibration_frequency_days,replacement_value=excluded.replacement_value,criticality=excluded.criticality,
    cctv_required=excluded.cctv_required,gps_required=excluded.gps_required,required_ppe=excluded.required_ppe,
    ownership_type=excluded.ownership_type,contractor_name=excluded.contractor_name,oem_manual_ref=excluded.oem_manual_ref,
    purchase_date=excluded.purchase_date,procurement_cost=excluded.procurement_cost,insured=excluded.insured,
    last_verified=excluded.last_verified,working_status=excluded.working_status,
    row_version=public.tools_workspace_equipment.row_version+1,updated_at=now()
  returning * into saved;
  event:=coalesce(payload->'event','{}'::jsonb);
  insert into public.tools_workspace_history(id,tool_id,tool_name,action,detail,actor_name,employee_id,employee_name,event_at,metadata)
  values(coalesce(nullif(event->>'id','')::uuid,gen_random_uuid()),saved.id,saved.name,payload->>'action',event->>'detail',payload->>'actor_name',nullif(event->>'employee_id','')::uuid,event->>'employee_name',coalesce(nullif(event->>'event_at','')::timestamptz,now()),coalesce(event->'metadata','{}'::jsonb));
  change_id:=coalesce(nullif(payload->>'change_id','')::uuid,gen_random_uuid());
  insert into public.tools_workspace_changes(id,tool_id,action,before_state,after_state,actor_name)
  values(change_id,saved.id,payload->>'action',coalesce(payload->'before_state','{}'::jsonb),to_jsonb(saved),payload->>'actor_name');
  result_value:=jsonb_build_object('tool',to_jsonb(saved),'change_id',change_id);
  insert into public.tools_workspace_idempotency(key,result) values(idem,result_value);
  return jsonb_build_object('replayed',false,'result',result_value);
end $$;
revoke all on function public.tools_workspace_apply_change(jsonb) from public;
grant execute on function public.tools_workspace_apply_change(jsonb) to service_role;

create or replace function public.tools_workspace_restore_change(change_key uuid,actor text,redo boolean default false)
returns jsonb language plpgsql security definer set search_path=public as $$
declare change_row public.tools_workspace_changes; desired jsonb; saved public.tools_workspace_equipment;
begin
  select * into change_row from public.tools_workspace_changes where id=change_key for update;
  if not found then raise exception 'CHANGE_NOT_FOUND' using errcode='P0001'; end if;
  if redo and change_row.undone_at is null then raise exception 'CHANGE_NOT_UNDONE' using errcode='P0001'; end if;
  if not redo and change_row.undone_at is not null then raise exception 'CHANGE_ALREADY_UNDONE' using errcode='P0001'; end if;
  perform 1 from public.tools_workspace_equipment where id=change_row.tool_id for update;
  desired:=case when redo then change_row.after_state else change_row.before_state end;
  insert into public.tools_workspace_equipment select * from jsonb_populate_record(null::public.tools_workspace_equipment,desired)
  on conflict(id) do update set
    register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,
    category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,
    section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,
    calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,
    site_code=case when desired ? 'site_code' then excluded.site_code else public.tools_workspace_equipment.site_code end,
    area_code=case when desired ? 'area_code' then excluded.area_code else public.tools_workspace_equipment.area_code end,
    department_code=case when desired ? 'department_code' then excluded.department_code else public.tools_workspace_equipment.department_code end,
    activity_code=case when desired ? 'activity_code' then excluded.activity_code else public.tools_workspace_equipment.activity_code end,
    home_storage_location=case when desired ? 'home_storage_location' then excluded.home_storage_location else public.tools_workspace_equipment.home_storage_location end,
    storage_conditions=case when desired ? 'storage_conditions' then excluded.storage_conditions else public.tools_workspace_equipment.storage_conditions end,
    maintenance_requirements=case when desired ? 'maintenance_requirements' then excluded.maintenance_requirements else public.tools_workspace_equipment.maintenance_requirements end,
    pre_use_check_required=case when desired ? 'pre_use_check_required' then excluded.pre_use_check_required else public.tools_workspace_equipment.pre_use_check_required end,
    weekly_inspection_required=case when desired ? 'weekly_inspection_required' then excluded.weekly_inspection_required else public.tools_workspace_equipment.weekly_inspection_required end,
    monthly_inspection_required=case when desired ? 'monthly_inspection_required' then excluded.monthly_inspection_required else public.tools_workspace_equipment.monthly_inspection_required end,
    quarterly_inspection_required=case when desired ? 'quarterly_inspection_required' then excluded.quarterly_inspection_required else public.tools_workspace_equipment.quarterly_inspection_required end,
    calibration_required=case when desired ? 'calibration_required' then excluded.calibration_required else public.tools_workspace_equipment.calibration_required end,
    calibration_frequency_days=case when desired ? 'calibration_frequency_days' then excluded.calibration_frequency_days else public.tools_workspace_equipment.calibration_frequency_days end,
    replacement_value=case when desired ? 'replacement_value' then excluded.replacement_value else public.tools_workspace_equipment.replacement_value end,
    criticality=case when desired ? 'criticality' then excluded.criticality else public.tools_workspace_equipment.criticality end,
    cctv_required=case when desired ? 'cctv_required' then excluded.cctv_required else public.tools_workspace_equipment.cctv_required end,
    gps_required=case when desired ? 'gps_required' then excluded.gps_required else public.tools_workspace_equipment.gps_required end,
    required_ppe=case when desired ? 'required_ppe' then excluded.required_ppe else public.tools_workspace_equipment.required_ppe end,
    ownership_type=case when desired ? 'ownership_type' then excluded.ownership_type else public.tools_workspace_equipment.ownership_type end,
    contractor_name=case when desired ? 'contractor_name' then excluded.contractor_name else public.tools_workspace_equipment.contractor_name end,
    oem_manual_ref=case when desired ? 'oem_manual_ref' then excluded.oem_manual_ref else public.tools_workspace_equipment.oem_manual_ref end,
    purchase_date=case when desired ? 'purchase_date' then excluded.purchase_date else public.tools_workspace_equipment.purchase_date end,
    procurement_cost=case when desired ? 'procurement_cost' then excluded.procurement_cost else public.tools_workspace_equipment.procurement_cost end,
    insured=case when desired ? 'insured' then excluded.insured else public.tools_workspace_equipment.insured end,
    last_verified=case when desired ? 'last_verified' then excluded.last_verified else public.tools_workspace_equipment.last_verified end,
    working_status=case when desired ? 'working_status' then excluded.working_status else public.tools_workspace_equipment.working_status end,
    row_version=public.tools_workspace_equipment.row_version+1,updated_at=now()
  returning * into saved;
  update public.tools_workspace_changes set undone_at=case when redo then null else now() end,undone_by=case when redo then null else actor end where id=change_key;
  insert into public.tools_workspace_history(tool_id,tool_name,action,detail,actor_name,metadata)
  values(saved.id,saved.name,case when redo then 'redo' else 'undo' end,change_row.action,actor,jsonb_build_object('change_id',change_key));
  return jsonb_build_object('tool',to_jsonb(saved),'change_id',change_key,'redo',redo);
end $$;
revoke all on function public.tools_workspace_restore_change(uuid,text,boolean) from public;
grant execute on function public.tools_workspace_restore_change(uuid,text,boolean) to service_role;

commit;
