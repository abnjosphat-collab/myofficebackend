-- ============================================================
-- AMS Document Folders Migration
-- Run in Supabase SQL Editor
--
-- Custom subfolders (created via the "+ New Folder" button inside a
-- document category) previously lived only in the browser's localStorage
-- -- invisible to other users/devices. This table makes them a real,
-- shared, persisted concept, same durability as the documents themselves.
-- The 7 top-level categories and each category's *default* subfolders
-- stay hardcoded in the frontend (app/documents/page.tsx) -- this table
-- only holds user-created custom folders.
-- ============================================================

create table if not exists public.document_folders (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  category_id   text not null,
  category_name text not null default '',
  name          text not null
);

create unique index if not exists document_folders_category_name_idx
  on public.document_folders(category_id, name);
create index if not exists document_folders_category_idx
  on public.document_folders(category_id);

drop trigger if exists document_folders_updated_at on public.document_folders;
create trigger document_folders_updated_at
  before update on public.document_folders
  for each row execute function public.set_updated_at();

-- RLS
alter table public.document_folders enable row level security;

drop policy if exists "auth_all_document_folders"    on public.document_folders;
drop policy if exists "service_role_document_folders" on public.document_folders;

create policy "auth_all_document_folders"
  on public.document_folders for all to authenticated using (true) with check (true);

create policy "service_role_document_folders"
  on public.document_folders for all to service_role using (true) with check (true);
