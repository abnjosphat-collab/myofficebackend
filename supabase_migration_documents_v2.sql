-- ============================================================
-- Document library upgrade: a real folder tree, files that belong to a real folder, search, trash, tags
-- File: backend/supabase_migration_documents_v2.sql
-- Date: 2026-10-19. Owner runs this in the Supabase SQL Editor, BEFORE deploying the new Documents page.
-- Safe to re-run (idempotent). It changes no file in storage and deletes no document.
--
-- Until this has been run the Documents page shows "needs the upgrade SQL" instead of failing, so run it first.
--
-- What it does
--  1. document_folders become a tree (parent_id), with a stable seed_key for the AMS structure, and a name that
--     must be unique within its parent.
--  2. Creates the top of the Asset Management System (AMS) tree: the root and sections 1 to 9. The rest of the tree
--     (the 75 folders under them) is created from the app by a manager ("Set up the AMS folders"), which skips every
--     folder that already exists.
--  3. Moves the folders people already made under the matching section (legacy category 1 to 7 = section 1 to 7).
--  4. Gives a real folder to every folder NAME that files were tagged with but that was never a real folder. Before
--     this, those files (for example 54 in "SOPs") could not be reached by browsing, only by search.
--  5. documents.folder_ref: each file now points at its folder by id, not by a typed name. Files that had a category
--     but no folder go into that section's folder.
--  6. New file columns: tags, extension, content hash (exact-duplicate guard), uploader, readable text, search text,
--     and a trash flag (deleted_at / deleted_by) so a deleted file can be put back.
--  7. Search indexes (pg_trgm), and the one-copy-per-folder and one-name-per-parent rules as unique indexes.
--
-- The old text columns (documents.category_id / category_name / folder_id) are kept, unchanged, for rollback only.
-- Nothing reads them any more.
--
-- Rollback (nothing in storage is touched, so nothing is lost):
--    ALTER TABLE public.documents DROP COLUMN folder_ref, DROP COLUMN ext, DROP COLUMN tags, DROP COLUMN content_hash,
--      DROP COLUMN uploaded_by, DROP COLUMN content_text, DROP COLUMN search_text, DROP COLUMN text_indexed_at,
--      DROP COLUMN deleted_at, DROP COLUMN deleted_by;
--    ALTER TABLE public.document_folders DROP COLUMN parent_id, DROP COLUMN seed_key, DROP COLUMN sort_order,
--      DROP COLUMN description, DROP COLUMN created_by;
-- ============================================================

create extension if not exists pg_trgm with schema extensions;

-- -- 1. Folders become a tree --------------------------------------------------------------------
alter table public.document_folders
  add column if not exists parent_id   uuid references public.document_folders(id) on delete restrict,
  add column if not exists seed_key    text,
  add column if not exists sort_order  integer not null default 1000,
  add column if not exists description text not null default '',
  add column if not exists created_by  text not null default '';

-- Folders made in the app have no legacy category.
alter table public.document_folders alter column category_id set default '';

-- A name used to be unique per category; it is now unique per parent folder (below), so two folders called "Reports"
-- can live in different places.
drop index if exists public.document_folders_category_name_idx;

create unique index if not exists document_folders_seed_key_idx
  on public.document_folders (seed_key) where seed_key is not null;
create index if not exists document_folders_parent_idx on public.document_folders (parent_id);

-- -- 2 + 3. The top of the AMS tree, and the folders people already made ------------------------
do $$
declare
  root_id uuid;
  sec record;
  sec_id uuid;
begin
  select id into root_id from public.document_folders where seed_key = 'ams';
  if root_id is null then
    -- a folder somebody already made with this name at the top level becomes the root
    select id into root_id from public.document_folders
      where parent_id is null and seed_key is null and lower(name) = lower('Asset Management System (AMS)') limit 1;
    if root_id is null then
      insert into public.document_folders (category_id, category_name, name, sort_order, seed_key, created_by)
      values ('', '', 'Asset Management System (AMS)', 10, 'ams', 'migration') returning id into root_id;
    else
      update public.document_folders set seed_key = 'ams', sort_order = 10 where id = root_id;
    end if;
  end if;

  for sec in
    select * from (values
      (1, '1. Organizational Context',                       'ams/1-organizational-context'),
      (2, '2. Leadership',                                   'ams/2-leadership'),
      (3, '3. Planning',                                     'ams/3-planning'),
      (4, '4. Support',                                      'ams/4-support'),
      (5, '5. Operation',                                    'ams/5-operation'),
      (6, '6. Performance Evaluation',                       'ams/6-performance-evaluation'),
      (7, '7. Improvement',                                  'ams/7-improvement'),
      (8, '8. Asset Portfolio (Mining-Specific)',            'ams/8-asset-portfolio-mining-specific'),
      (9, '9. AMS Governance (Optional but Recommended)',    'ams/9-ams-governance-optional-but-recommended')
    ) as t(num, name, key)
  loop
    select id into sec_id from public.document_folders where seed_key = sec.key;
    if sec_id is null then
      insert into public.document_folders (category_id, category_name, name, parent_id, sort_order, seed_key, created_by)
      values ('', '', sec.name, root_id, sec.num * 10, sec.key, 'migration');
    end if;
  end loop;
end $$;

-- A folder that files were tagged with but that never had a row (4): make it real, under its section.
insert into public.document_folders (category_id, category_name, name, parent_id, sort_order, created_by)
select d.category_id, min(d.category_name), d.folder_id,
       coalesce(
         (select s.id from public.document_folders s where s.seed_key like 'ams/' || d.category_id || '-%'),
         (select r.id from public.document_folders r where r.seed_key = 'ams')),
       1000, 'migration'
from public.documents d
where d.folder_id is not null and d.folder_id <> ''
  and not exists (select 1 from public.document_folders f where f.category_id = d.category_id and f.name = d.folder_id)
group by d.category_id, d.folder_id;

-- The folders people already made (3) go under their section. Section n = legacy category n.
update public.document_folders f
set parent_id = s.id
from public.document_folders s
where f.parent_id is null and f.seed_key is null and f.category_id ~ '^[1-9]$'
  and s.seed_key like 'ams/' || f.category_id || '-%' and s.id <> f.id;

-- Anything still at the top level that is not the AMS root stays at the top level (a folder somebody made there).

-- One name per parent, case-insensitive. If this fails, two folders under one parent share a name apart from capitals:
-- rename one of them and run the file again.
create unique index if not exists document_folders_one_name_per_parent
  on public.document_folders (coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), lower(name));

-- -- 5 + 6. Files ----------------------------------------------------------------------------------
alter table public.documents
  add column if not exists folder_ref       uuid references public.document_folders(id) on delete restrict,
  add column if not exists ext              text not null default '',
  add column if not exists tags             text[] not null default '{}',
  add column if not exists content_hash     text,
  add column if not exists uploaded_by      text not null default '',
  add column if not exists content_text     text not null default '',
  add column if not exists search_text      text not null default '',
  add column if not exists text_indexed_at  timestamptz,
  add column if not exists deleted_at       timestamptz,
  add column if not exists deleted_by       text not null default '';

comment on column public.documents.category_id   is 'LEGACY (kept for rollback): replaced by folder_ref';
comment on column public.documents.category_name is 'LEGACY (kept for rollback): replaced by folder_ref';
comment on column public.documents.folder_id     is 'LEGACY (kept for rollback): a typed folder NAME, replaced by folder_ref';
comment on column public.documents.folder_path   is 'Display path of the folder, "A > B > C"; kept up to date by the app and searched';
comment on column public.documents.content_text  is 'Words read from the file at upload (first ~30,000 characters), so it can be found by what it says';

-- search_text = everything about a file that people type or choose, kept current by a trigger.
create or replace function public.documents_set_search_text() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.search_text := lower(concat_ws(' ', new.name, new.original_name, new.description,
                                     array_to_string(new.tags, ' '), new.folder_path, new.ext, new.uploaded_by));
  return new;
end $$;

drop trigger if exists documents_search_text on public.documents;
create trigger documents_search_text before insert or update on public.documents
  for each row execute function public.documents_set_search_text();

-- Reading the words out of a file is not an edit of the file: it must not move "updated_at" (the Updated sort and the
-- stale-edit check both rely on it). Every other change still does.
drop trigger if exists documents_updated_at on public.documents;
create trigger documents_updated_at before update on public.documents
  for each row when (old.content_text is not distinct from new.content_text)
  execute function public.set_updated_at();

-- Backfill without touching updated_at ("recently updated" would otherwise say every file changed today).
alter table public.documents disable trigger user;

update public.documents
set ext = lower(coalesce(substring(original_name from '\.([^.]+)$'), ''))
where ext = '';

-- each file points at its folder: the named folder, else the section of its old category
update public.documents d
set folder_ref = f.id
from public.document_folders f
where d.folder_ref is null and d.folder_id is not null and d.folder_id <> ''
  and f.category_id = d.category_id and f.name = d.folder_id;

update public.documents d
set folder_ref = s.id
from public.document_folders s
where d.folder_ref is null and (d.folder_id is null or d.folder_id = '')
  and d.category_id ~ '^[1-9]$' and s.seed_key like 'ams/' || d.category_id || '-%';

with recursive paths as (
  select id, name::text as path from public.document_folders where parent_id is null
  union all
  select f.id, p.path || ' > ' || f.name from public.document_folders f join paths p on f.parent_id = p.id
)
update public.documents d set folder_path = p.path from paths p where d.folder_ref = p.id;

update public.documents set folder_path = '' where folder_ref is null;

update public.documents
set search_text = lower(concat_ws(' ', name, original_name, description, array_to_string(tags, ' '), folder_path, ext, uploaded_by));

alter table public.documents enable trigger user;

-- -- 7. Indexes and rules -----------------------------------------------------------------------
create index if not exists documents_folder_ref_idx   on public.documents (folder_ref) where deleted_at is null;
create index if not exists documents_deleted_at_idx   on public.documents (deleted_at) where deleted_at is not null;
create index if not exists documents_ext_idx          on public.documents (ext);
create index if not exists documents_tags_idx         on public.documents using gin (tags);
create index if not exists documents_search_trgm_idx  on public.documents using gin (search_text extensions.gin_trgm_ops);
create index if not exists documents_content_trgm_idx on public.documents using gin (content_text extensions.gin_trgm_ops);

-- The same file (identical content) cannot be in one folder twice. A file with no recorded hash (everything uploaded
-- before this) is not affected. The app checks this first and says where the copy is; this is the backstop for two people
-- uploading at the same moment.
create unique index if not exists documents_one_copy_per_folder
  on public.documents (coalesce(folder_ref, '00000000-0000-0000-0000-000000000000'::uuid), content_hash)
  where deleted_at is null and content_hash is not null;

create index if not exists document_activities_document_idx on public.document_activities (document_id, created_at desc);

-- Only the backend (service role) reads or writes these tables.
alter table public.documents           enable row level security;
alter table public.document_folders    enable row level security;
alter table public.document_activities enable row level security;

-- ============================================================
-- Check afterwards (read-only):
--   select count(*) filter (where folder_ref is null) as unfiled, count(*) as total from public.documents;
--   select name, (select count(*) from public.documents d where d.folder_ref = f.id) as files
--     from public.document_folders f where f.parent_id = (select id from public.document_folders where seed_key = 'ams')
--     order by sort_order;
-- Then open Documents and press "Set up the AMS folders" (manager) to create the rest of the tree.
-- ============================================================
