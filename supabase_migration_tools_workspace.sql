-- Tools & Equipment workspace. Preserves existing portable-tools data, then retires that schema.
-- Run as one script in Supabase SQL Editor; PostgreSQL rolls the transaction back on error.
begin;
create extension if not exists pgcrypto;

create table if not exists public.tools_workspace_accounts (id uuid primary key default gen_random_uuid(),name text not null,username text not null unique,salt text not null,password_hash text not null,role text not null default 'viewer' check(role in ('admin','issuer','viewer')),department text,can_issue boolean not null default false,created_at timestamptz not null default now());
create table if not exists public.tools_workspace_sessions (id uuid primary key default gen_random_uuid(),token_hash text not null unique,account_id uuid not null references public.tools_workspace_accounts(id) on delete cascade,created_at timestamptz not null default now(),expires_at timestamptz not null);
create table if not exists public.tools_workspace_employees (id uuid primary key default gen_random_uuid(),employee_number text not null unique,name text not null,department text not null,job_title text,active boolean not null default true,created_by text not null,created_at timestamptz not null default now(),updated_at timestamptz);
create table if not exists public.tools_workspace_equipment (
 id uuid primary key default gen_random_uuid(),register_number text not null unique,name text not null,make_model text,serial_number text,category text,equipment_kind text not null default 'other-equipment',storage_location text not null,department text not null default 'Engineering',section text,status text not null default 'available' check(status in ('available','issued','overdue','attention')),condition text not null default 'Good',notes text,approval_ref text,calibration text,specifications jsonb not null default '{}'::jsonb,archived boolean not null default false,custody jsonb,row_version integer not null default 1,created_by text not null,created_at timestamptz not null default now(),updated_at timestamptz not null default now()
);
create table if not exists public.tools_workspace_history (id uuid primary key default gen_random_uuid(),tool_id uuid references public.tools_workspace_equipment(id) on delete restrict,tool_name text not null,action text not null,detail text,actor_name text not null,employee_id uuid references public.tools_workspace_employees(id),employee_name text,event_at timestamptz not null default now(),metadata jsonb not null default '{}'::jsonb);
create table if not exists public.tools_workspace_changes (id uuid primary key default gen_random_uuid(),tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,action text not null,before_state jsonb not null,after_state jsonb not null,actor_name text not null,created_at timestamptz not null default now(),undone_at timestamptz,undone_by text);
create table if not exists public.tools_workspace_idempotency (key text primary key,result jsonb not null,created_at timestamptz not null default now());
create table if not exists public.tools_workspace_evidence (id uuid primary key default gen_random_uuid(),tool_id uuid not null references public.tools_workspace_equipment(id) on delete restrict,storage_path text not null unique,original_name text not null,content_type text not null,size_bytes bigint not null,uploaded_by text not null,uploaded_at timestamptz not null default now(),removed_at timestamptz);
create table if not exists public.tools_workspace_usage (id uuid primary key default gen_random_uuid(),event text not null,detail text,account_id uuid references public.tools_workspace_accounts(id),account_name text,created_at timestamptz not null default now());
create table if not exists public.tools_workspace_errors (id uuid primary key default gen_random_uuid(),message text not null,source text,stack text,account_id uuid references public.tools_workspace_accounts(id),account_name text,created_at timestamptz not null default now());
create table if not exists public.tools_workspace_feedback (id uuid primary key default gen_random_uuid(),text text,audio_path text,audio_filename text,audio_content_type text,audio_size bigint not null default 0,account_id uuid references public.tools_workspace_accounts(id),account_name text,created_at timestamptz not null default now(),check(text is not null or audio_path is not null or audio_filename is not null));
create table if not exists public.tools_workspace_notification_reads (account_id uuid not null references public.tools_workspace_accounts(id) on delete cascade,alert_key text not null,read_at timestamptz not null default now(),primary key(account_id,alert_key));
create unique index if not exists tools_workspace_equipment_number_uidx on public.tools_workspace_equipment(lower(register_number));
create unique index if not exists tools_workspace_employee_number_uidx on public.tools_workspace_employees(lower(employee_number));
create index if not exists tools_workspace_history_tool_idx on public.tools_workspace_history(tool_id,event_at desc);
create index if not exists tools_workspace_changes_actor_idx on public.tools_workspace_changes(actor_name,created_at desc);
create index if not exists tools_workspace_notification_reads_account_idx on public.tools_workspace_notification_reads(account_id,read_at desc);

-- Copy the live register, open custody and movement trail before old objects are removed.
insert into public.tools_workspace_equipment (id,register_number,name,make_model,serial_number,category,equipment_kind,storage_location,department,section,status,condition,notes,approval_ref,calibration,archived,custody,row_version,created_by,created_at,updated_at)
select p.id,p.tool_number,p.description,trim(concat_ws(' ',p.make,p.model)),p.serial_number,p.category,'other-equipment',coalesce(c.location,p.storage_location,'Unspecified'),p.owning_department,p.owning_section,
 case when p.lifecycle_status='hold' or p.physical_condition<>'serviceable' then 'attention' when c.tool_id is not null and c.due_at<now() then 'overdue' when c.tool_id is not null then 'issued' else 'available' end,
 case p.physical_condition when 'serviceable' then 'Good' when 'missing_components' then 'Missing parts' else initcap(p.physical_condition) end,p.notes,p.gm_approval_ref,p.calibration_certificate_ref,(p.lifecycle_status='archived'),
 case when c.tool_id is null then null else jsonb_build_object('employee_number',c.holder_employee_id,'employee_name',c.holder_name,'department',c.holder_department,'issued_at',c.issued_at,'original_due_at',c.original_due_at,'expected_return_at',c.due_at,'job_reference',c.work_order_ref,'location',c.location,'legacy_issue_id',c.issue_id) end,
 p.row_version,coalesce(p.created_by_email,'Legacy register'),p.created_at,p.updated_at
from public.portable_tools p left join public.portable_tool_custody c on c.tool_id=p.id on conflict(id) do nothing;
insert into public.tools_workspace_history (id,tool_id,tool_name,action,detail,actor_name,employee_name,event_at,metadata)
select m.id,m.tool_id,coalesce(p.description,'Tools & Equipment'),m.event_type,m.reason,coalesce(m.recorder_email,'Legacy register'),coalesce(m.to_holder_name,m.from_holder_name),m.event_at,coalesce(m.metadata,'{}'::jsonb)||jsonb_build_object('legacy_issue_id',m.issue_id,'from_location',m.from_location,'to_location',m.to_location,'due_at',m.due_at)
from public.portable_tool_movements m left join public.portable_tools p on p.id=m.tool_id where m.tool_id is not null on conflict(id) do nothing;

create or replace function public.tools_workspace_apply_change(payload jsonb) returns jsonb language plpgsql security definer set search_path=public as $$
declare idem text; result_value jsonb; current_version int; expected_version int; saved public.tools_workspace_equipment; event jsonb; change_id uuid;
begin
 idem:=nullif(trim(payload->>'idempotency_key'),''); if idem is null then raise exception 'IDEMPOTENCY_REQUIRED' using errcode='P0001'; end if;
 select result into result_value from public.tools_workspace_idempotency where key=idem; if found then return jsonb_build_object('replayed',true,'result',result_value); end if;
 expected_version:=nullif(payload->>'expected_version','')::int;
 if payload->>'tool_id' is not null then select row_version into current_version from public.tools_workspace_equipment where id=(payload->>'tool_id')::uuid for update;
  if current_version is null then raise exception 'TOOL_NOT_FOUND' using errcode='P0001'; end if;
  if expected_version is not null and current_version<>expected_version then raise exception 'STALE_VERSION' using errcode='P0001'; end if;
 end if;
 insert into public.tools_workspace_equipment select * from jsonb_populate_record(null::public.tools_workspace_equipment,payload->'after_state')
 on conflict(id) do update set register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,row_version=public.tools_workspace_equipment.row_version+1,updated_at=now() returning * into saved;
 event:=coalesce(payload->'event','{}'::jsonb);
 insert into public.tools_workspace_history(id,tool_id,tool_name,action,detail,actor_name,employee_id,employee_name,event_at,metadata) values(coalesce(nullif(event->>'id','')::uuid,gen_random_uuid()),saved.id,saved.name,payload->>'action',event->>'detail',payload->>'actor_name',nullif(event->>'employee_id','')::uuid,event->>'employee_name',coalesce(nullif(event->>'event_at','')::timestamptz,now()),coalesce(event->'metadata','{}'::jsonb));
 change_id:=coalesce(nullif(payload->>'change_id','')::uuid,gen_random_uuid());
 insert into public.tools_workspace_changes(id,tool_id,action,before_state,after_state,actor_name) values(change_id,saved.id,payload->>'action',coalesce(payload->'before_state','{}'::jsonb),to_jsonb(saved),payload->>'actor_name');
 result_value:=jsonb_build_object('tool',to_jsonb(saved),'change_id',change_id); insert into public.tools_workspace_idempotency(key,result) values(idem,result_value);
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
 on conflict(id) do update set register_number=excluded.register_number,name=excluded.name,make_model=excluded.make_model,serial_number=excluded.serial_number,category=excluded.category,equipment_kind=excluded.equipment_kind,storage_location=excluded.storage_location,department=excluded.department,section=excluded.section,status=excluded.status,condition=excluded.condition,notes=excluded.notes,approval_ref=excluded.approval_ref,calibration=excluded.calibration,specifications=excluded.specifications,archived=excluded.archived,custody=excluded.custody,row_version=public.tools_workspace_equipment.row_version+1,updated_at=now() returning * into saved;
 update public.tools_workspace_changes set undone_at=case when redo then null else now() end,undone_by=case when redo then null else actor end where id=change_key;
 insert into public.tools_workspace_history(tool_id,tool_name,action,detail,actor_name,metadata) values(saved.id,saved.name,case when redo then 'redo' else 'undo' end,change_row.action,actor,jsonb_build_object('change_id',change_key));
 return jsonb_build_object('tool',to_jsonb(saved),'change_id',change_key,'redo',redo);
end $$;
revoke all on function public.tools_workspace_restore_change(uuid,text,boolean) from public;
grant execute on function public.tools_workspace_restore_change(uuid,text,boolean) to service_role;

alter table public.tools_workspace_accounts enable row level security; alter table public.tools_workspace_sessions enable row level security; alter table public.tools_workspace_employees enable row level security; alter table public.tools_workspace_equipment enable row level security; alter table public.tools_workspace_history enable row level security; alter table public.tools_workspace_changes enable row level security; alter table public.tools_workspace_idempotency enable row level security; alter table public.tools_workspace_evidence enable row level security; alter table public.tools_workspace_usage enable row level security; alter table public.tools_workspace_errors enable row level security; alter table public.tools_workspace_feedback enable row level security; alter table public.tools_workspace_notification_reads enable row level security;
insert into storage.buckets(id,name,public,allowed_mime_types) values('tools-workspace-evidence','tools-workspace-evidence',false,array['image/jpeg','image/png','image/webp','image/gif','image/avif','application/pdf']) on conflict(id) do update set public=false,allowed_mime_types=excluded.allowed_mime_types,file_size_limit=null;
insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types) values('tools-workspace-feedback','tools-workspace-feedback',false,26214400,array['audio/webm','audio/ogg','audio/mp4','audio/mpeg']) on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,allowed_mime_types=excluded.allowed_mime_types;

-- Retire only after every preserving insert and function definition above succeeded.
drop function if exists public.portable_tools_commit_bundle(jsonb);
drop table if exists public.portable_tool_evidence; drop table if exists public.portable_tool_notifications; drop table if exists public.portable_tool_notification_recipients; drop table if exists public.portable_tool_job_runs; drop table if exists public.portable_tool_corrections; drop table if exists public.portable_tool_idempotency; drop table if exists public.portable_tool_movements; drop table if exists public.portable_tool_custody; drop table if exists public.portable_tool_issue_lines; drop table if exists public.portable_tool_issues; drop table if exists public.portable_tools;
-- The retired storage bucket is removed after commit through Supabase's Storage API.
commit;
