-- Portable Tools SOP controls: numbering, competencies, inspections, storage and gate passes.
begin;

alter table public.tools_workspace_accounts
  add column if not exists approval_roles text[] not null default '{}',
  add column if not exists signing_pin_salt text,
  add column if not exists signing_pin_hash text;

alter table public.tools_workspace_equipment
  add column if not exists site_code text not null default 'PP',
  add column if not exists area_code text not null default 'UG',
  add column if not exists department_code text,
  add column if not exists activity_code text,
  add column if not exists home_storage_location text,
  add column if not exists storage_conditions text,
  add column if not exists maintenance_requirements text,
  add column if not exists pre_use_check_required boolean not null default true,
  add column if not exists weekly_inspection_required boolean not null default false,
  add column if not exists monthly_inspection_required boolean not null default true,
  add column if not exists quarterly_inspection_required boolean not null default true,
  add column if not exists calibration_required boolean not null default false,
  add column if not exists calibration_frequency_days integer,
  add column if not exists replacement_value numeric(14,2),
  add column if not exists criticality text not null default 'standard',
  add column if not exists cctv_required boolean not null default false,
  add column if not exists gps_required boolean not null default false,
  add column if not exists required_ppe text[] not null default '{}';
alter table public.tools_workspace_equipment
  add column if not exists ownership_type text not null default 'company' check(ownership_type in ('company','contractor')),
  add column if not exists contractor_name text,
  add column if not exists oem_manual_ref text;

update public.tools_workspace_equipment
set home_storage_location = coalesce(home_storage_location, storage_location),
    department_code = coalesce(department_code,
      case department
        when 'Engineering' then 'ENG'
        when 'Mining' then 'MIN'
        when 'Mine Technical Services' then 'MTS'
        when 'Shared Services' then 'SS'
        when 'IT' then 'IT'
        else upper(left(regexp_replace(department, '[^A-Za-z]', '', 'g'), 3))
      end)
where home_storage_location is null or department_code is null;

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
    row_version=public.tools_workspace_equipment.row_version+1,updated_at=now()
  returning * into saved;
  update public.tools_workspace_changes set undone_at=case when redo then null else now() end,undone_by=case when redo then null else actor end where id=change_key;
  insert into public.tools_workspace_history(tool_id,tool_name,action,detail,actor_name,metadata)
  values(saved.id,saved.name,case when redo then 'redo' else 'undo' end,change_row.action,actor,jsonb_build_object('change_id',change_key));
  return jsonb_build_object('tool',to_jsonb(saved),'change_id',change_key,'redo',redo);
end $$;
revoke all on function public.tools_workspace_restore_change(uuid,text,boolean) from public;
grant execute on function public.tools_workspace_restore_change(uuid,text,boolean) to service_role;

create table if not exists public.tools_workspace_number_sequences (
  prefix text primary key,
  last_number integer not null default 0,
  updated_at timestamptz not null default now()
);

create or replace function public.tools_workspace_next_register_number(prefix_value text)
returns text language plpgsql security definer set search_path=public as $$
declare next_value integer;
begin
  insert into public.tools_workspace_number_sequences(prefix,last_number)
  values(upper(prefix_value),1)
  on conflict(prefix) do update set last_number=public.tools_workspace_number_sequences.last_number+1,updated_at=now()
  returning last_number into next_value;
  return upper(prefix_value)||'-'||lpad(next_value::text,2,'0');
end $$;
revoke all on function public.tools_workspace_next_register_number(text) from public;
grant execute on function public.tools_workspace_next_register_number(text) to service_role;

create table if not exists public.tools_workspace_competencies (
  id uuid primary key default gen_random_uuid(),
  employee_id uuid not null references public.tools_workspace_employees(id) on delete cascade,
  tool_id uuid references public.tools_workspace_equipment(id) on delete cascade,
  category text,
  trained boolean not null default false,
  qualified boolean not null default false,
  authorized boolean not null default false,
  training_certificate_ref text,
  training_expires_at timestamptz,
  qualification_ref text,
  qualification_expires_at timestamptz,
  authorized_by text,
  authorization_expires_at timestamptz,
  notes text,
  created_by text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check(tool_id is not null or nullif(trim(category),'') is not null)
);
create unique index if not exists tools_workspace_competency_scope_uidx
  on public.tools_workspace_competencies(employee_id,coalesce(tool_id::text,''),coalesce(lower(category),''));
create index if not exists tools_workspace_competency_tool_idx on public.tools_workspace_competencies(tool_id);
create index if not exists tools_workspace_competency_employee_idx on public.tools_workspace_competencies(employee_id);

create table if not exists public.tools_workspace_inspections (
  id uuid primary key default gen_random_uuid(),
  tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  inspection_type text not null check(inspection_type in ('pre_use','weekly','monthly','quarterly','calibration','maintenance','storage_audit','repair')),
  outcome text not null check(outcome in ('passed','conditional','failed')),
  inspected_at timestamptz not null default now(),
  next_due_at timestamptz,
  inspector_account_id uuid references public.tools_workspace_accounts(id),
  inspector_name text not null,
  colour_code text,
  condition text,
  defects text,
  notes text,
  repair_quote numeric(14,2),
  new_equipment_price numeric(14,2),
  repair_eligible boolean,
  created_at timestamptz not null default now()
);
create index if not exists tools_workspace_inspection_tool_idx on public.tools_workspace_inspections(tool_id,inspected_at desc);
create index if not exists tools_workspace_inspection_due_idx on public.tools_workspace_inspections(next_due_at);

create table if not exists public.tools_workspace_incidents (
  id uuid primary key default gen_random_uuid(),
  tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  incident_type text not null check(incident_type in ('lost','damaged','stolen','missing_components','late_return')),
  occurred_at timestamptz not null,
  reported_at timestamptz not null default now(),
  reported_by_account_id uuid references public.tools_workspace_accounts(id),
  reported_by text not null,
  employee_id uuid references public.tools_workspace_employees(id),
  explanation text not null,
  investigation_due_at timestamptz not null,
  status text not null default 'open' check(status in ('open','investigating','closed')),
  negligence_confirmed boolean,
  replacement_cost numeric(14,2),
  recovery_months integer,
  investigation_outcome text,
  closed_at timestamptz,
  closed_by text
);
create index if not exists tools_workspace_incident_status_idx on public.tools_workspace_incidents(status,investigation_due_at);

create table if not exists public.tools_workspace_gate_passes (
  id uuid primary key default gen_random_uuid(),
  pass_number text not null unique,
  movement_scope text not null check(movement_scope in ('internal','external')),
  department text not null,
  destination text not null,
  purpose text not null,
  expected_out_at timestamptz not null,
  expected_return_at timestamptz,
  finance_required boolean not null default false,
  status text not null default 'draft' check(status in ('draft','pending','approved','rejected','cancelled','closed')),
  requested_by_account_id uuid references public.tools_workspace_accounts(id),
  requested_by text not null,
  requested_at timestamptz not null default now(),
  approved_at timestamptz,
  closed_at timestamptz,
  notes text,
  verification_code text not null
);
create table if not exists public.tools_workspace_gate_pass_items (
  gate_pass_id uuid not null references public.tools_workspace_gate_passes(id) on delete cascade,
  tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,
  primary key(gate_pass_id,tool_id)
);
create table if not exists public.tools_workspace_gate_pass_approvals (
  id uuid primary key default gen_random_uuid(),
  gate_pass_id uuid not null references public.tools_workspace_gate_passes(id) on delete cascade,
  step_order integer not null,
  role text not null check(role in ('hos','hod','security','finance','general_manager')),
  status text not null default 'pending' check(status in ('pending','approved','rejected')),
  signer_account_id uuid references public.tools_workspace_accounts(id),
  signer_name text,
  signed_at timestamptz,
  comment text,
  signature_method text,
  unique(gate_pass_id,role)
);
create index if not exists tools_workspace_gate_pass_status_idx on public.tools_workspace_gate_passes(status,requested_at desc);
create index if not exists tools_workspace_gate_pass_approval_idx on public.tools_workspace_gate_pass_approvals(gate_pass_id,step_order);

alter table public.tools_workspace_number_sequences enable row level security;
alter table public.tools_workspace_competencies enable row level security;
alter table public.tools_workspace_inspections enable row level security;
alter table public.tools_workspace_incidents enable row level security;
alter table public.tools_workspace_gate_passes enable row level security;
alter table public.tools_workspace_gate_pass_items enable row level security;
alter table public.tools_workspace_gate_pass_approvals enable row level security;

commit;
