-- Documents: Quick print (owner request, 29 Oct 2026).
-- 1. Four columns on documents for a file's print copy: a PDF kept with a Word or Excel paper so Print sends the exact
--    pages instead of the browser's redrawing of them. Every existing row gets null (no print copy); nothing else changes.
-- 2. The Quick print folder at the top of the library, known by seed_key 'quick-print' (so renaming it keeps it). A
--    top-level folder somebody already called "Quick print" is adopted instead of making a second one.
-- Must run BEFORE the backend that reads the columns is live (the library lists them). Until then that backend shows
-- "needs its one-time upgrade" naming this file, instead of failing.
-- Rerunnable: IF NOT EXISTS on the columns; the folder is only made when no 'quick-print' folder exists.
-- No file in storage is touched and no document row changes value.
--
-- Rollback: delete from public.document_folders where seed_key = 'quick-print';  (only once it holds no files)
--           alter table public.documents drop column print_copy_path, drop column print_copy_url,
--             drop column print_copy_name, drop column print_copy_size;
begin;

do $$
begin
  if to_regclass('public.documents') is null or to_regclass('public.document_folders') is null then
    raise exception 'documents or document_folders is missing. Nothing was changed.';
  end if;
  if not exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'document_folders' and column_name = 'seed_key') then
    raise exception 'Run supabase_migration_documents_v2.sql first. Nothing was changed.';
  end if;
end $$;

alter table public.documents add column if not exists print_copy_path text;
alter table public.documents add column if not exists print_copy_url  text;
alter table public.documents add column if not exists print_copy_name text;
alter table public.documents add column if not exists print_copy_size bigint;

do $$
declare
  folder_id uuid;
begin
  select id into folder_id from public.document_folders where seed_key = 'quick-print';
  if folder_id is null then
    select id into folder_id from public.document_folders
      where parent_id is null and seed_key is null and lower(name) = lower('Quick print') limit 1;
    if folder_id is null then
      insert into public.document_folders (category_id, category_name, name, sort_order, seed_key, description, created_by)
      values ('', '', 'Quick print', 0, 'quick-print', 'Papers printed often. Print straight from here, no download.', 'migration');
    else
      update public.document_folders set seed_key = 'quick-print', sort_order = 0 where id = folder_id;
    end if;
  end if;
end $$;

do $$
declare
  cols int;
  folders int;
  with_copy int;
begin
  select count(*) into cols from information_schema.columns
  where table_schema = 'public' and table_name = 'documents'
    and column_name in ('print_copy_path', 'print_copy_url', 'print_copy_name', 'print_copy_size');
  if cols <> 4 then
    raise exception 'Expected the four print-copy columns, found %. Rolled back.', cols;
  end if;
  select count(*) into folders from public.document_folders where seed_key = 'quick-print';
  if folders <> 1 then
    raise exception 'Expected one Quick print folder, found %. Rolled back.', folders;
  end if;
  -- Fresh columns must not hold anything yet: only the app writes them.
  select count(*) into with_copy from public.documents where print_copy_path is not null;
  raise notice 'Print-copy columns present; Quick print folder ready; % documents have a print copy.', with_copy;
end $$;

commit;
