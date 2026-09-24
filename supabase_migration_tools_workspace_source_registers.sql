-- Preserve original departmental registers in private permanent storage.
begin;

create table if not exists public.tools_workspace_source_registers (
  id uuid primary key default gen_random_uuid(),
  department text not null,
  notes text,
  storage_path text not null unique,
  original_name text not null,
  content_type text not null,
  size_bytes bigint not null,
  uploaded_by_account_id uuid references public.tools_workspace_accounts(id),
  uploaded_by text not null,
  uploaded_at timestamptz not null default now()
);

create index if not exists tools_workspace_source_registers_uploaded_idx
  on public.tools_workspace_source_registers(uploaded_at desc);

alter table public.tools_workspace_source_registers enable row level security;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values(
  'tools-workspace-source-registers',
  'tools-workspace-source-registers',
  false,
  52428800,
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-excel.sheet.macroenabled.12',
    'application/vnd.ms-excel',
    'text/csv',
    'application/csv',
    'text/plain',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'application/vnd.oasis.opendocument.spreadsheet',
    'application/vnd.oasis.opendocument.text',
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/octet-stream'
  ]
)
on conflict(id) do update
set public=false,
    file_size_limit=excluded.file_size_limit,
    allowed_mime_types=excluded.allowed_mime_types;

commit;
