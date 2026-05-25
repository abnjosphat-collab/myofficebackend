-- ============================================================
-- AMS Documents Table Migration
-- Run in Supabase SQL Editor
-- ============================================================

drop table if exists public.documents cascade;

create table public.documents (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  name          text not null,                              -- display name (user-editable)
  original_name text not null default '',                   -- original filename
  storage_path  text not null default '',
  file_url      text not null default '',
  file_size     bigint not null default 0,
  mime_type     text not null default 'application/octet-stream',
  file_type     text not null default 'file',               -- pdf | document | spreadsheet | image | video | audio | archive | file
  category_id   text not null default '',
  category_name text not null default '',
  folder_id     text,                                       -- null = category root
  folder_path   text not null default '',
  starred       boolean not null default false,
  description   text not null default ''                    -- comments
);

create index if not exists documents_category_folder_idx on public.documents(category_id, folder_id);
create index if not exists documents_created_at_idx      on public.documents(created_at desc);
create index if not exists documents_starred_idx         on public.documents(starred) where starred = true;

-- Auto-update updated_at
drop trigger if exists documents_updated_at on public.documents;
create trigger documents_updated_at
  before update on public.documents
  for each row execute function public.set_updated_at();

-- RLS
alter table public.documents enable row level security;

drop policy if exists "auth_all_documents"       on public.documents;
drop policy if exists "service_role_documents"   on public.documents;

create policy "auth_all_documents"
  on public.documents for all to authenticated using (true) with check (true);

create policy "service_role_documents"
  on public.documents for all to service_role using (true) with check (true);

-- ============================================================
-- Storage bucket
insert into storage.buckets (id, name, public)
values ('ams-documents', 'ams-documents', true)
on conflict do nothing;
-- ============================================================
