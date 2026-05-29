-- ============================================================
-- SHEQ Inspections — Before / After photo columns
-- Run in Supabase Dashboard → SQL Editor
-- ============================================================

-- Add photo URL arrays to sheq_inspections
alter table public.sheq_inspections
  add column if not exists before_photos jsonb not null default '[]',
  add column if not exists after_photos  jsonb not null default '[]';

-- Create the Supabase Storage bucket (run once — safe to re-run)
-- NOTE: create the bucket manually in Storage → New Bucket if this errors
insert into storage.buckets (id, name, public)
values ('inspection-photos', 'inspection-photos', true)
on conflict (id) do nothing;

-- Allow authenticated users to upload/read
drop policy if exists "auth_upload_inspection_photos"  on storage.objects;
drop policy if exists "auth_select_inspection_photos"  on storage.objects;
drop policy if exists "auth_delete_inspection_photos"  on storage.objects;
drop policy if exists "public_read_inspection_photos"  on storage.objects;

create policy "public_read_inspection_photos"
  on storage.objects for select
  using (bucket_id = 'inspection-photos');

create policy "auth_upload_inspection_photos"
  on storage.objects for insert to authenticated
  with check (bucket_id = 'inspection-photos');

create policy "auth_delete_inspection_photos"
  on storage.objects for delete to authenticated
  using (bucket_id = 'inspection-photos');

-- ============================================================
