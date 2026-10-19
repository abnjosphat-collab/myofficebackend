# Document library

The place every document lives so that none is "lost" in email, WhatsApp, OneDrive or a Windows folder. Files are
kept in folders (the Asset Management System structure and any folder a person makes), found by one search, and
previewed, opened and printed in the page without downloading first.

Code: `app/routers/documents.py` (files), `app/routers/document_folders.py` (folders), `app/documents_support.py`
(shared database helpers), `app/document_rules.py` (pure rules), `app/document_structure.py` (the AMS tree).
Page: `frontend/app/documents/`.

## The model

| Thing | Table / column | Notes |
|---|---|---|
| Folder | `document_folders` (`parent_id`, `seed_key`, `sort_order`) | A real tree, up to 8 levels. A name is unique within its parent, ignoring case (a unique index backs it). |
| File | `documents` (`folder_ref` -> folder) | Belongs to a folder by id, never a typed name. `folder_ref` null means **Unfiled**. |
| Trash | `documents.deleted_at`, `deleted_by` | Delete moves to the trash. Only a manager deletes for good, and only from the trash. |
| Tags | `documents.tags text[]` | Saved lower case, one space between words, at most 20 of 40 characters. |
| Duplicates | `documents.content_hash` | The same content cannot be in one folder twice (unique index `documents_one_copy_per_folder`). |
| Search | `search_text` (trigger-maintained), `content_text` | Both trigram-indexed (`pg_trgm`). |
| History | `document_activities` | Who uploaded, edited, moved, trashed, restored or purged what. Survives a permanent delete. |

Legacy columns (`category_id`, `category_name`, `folder_id`) are kept for rollback and read by nothing.

## Rules (and why)

- **Safe to lose nothing.** Delete = trash with an Undo. A folder is deleted only when empty (files in the trash count);
  deleting a folder never deletes files. Purge removes the stored file first, then the row, then writes the history line.
- **Who and when come from the sign-in.** `uploaded_by`, `deleted_by`, the history actor: never from the browser.
- **The browser's claim about a file's type is not trusted.** The type a file is served with comes from its extension
  (`content_type_for`), so a ".txt" sent as `text/html` is not served as a web page from the storage address. Executables,
  scripts and web pages (`.exe .bat .sh .js .html ...`) are not accepted.
- **A search cannot become another query.** Each word is escaped for LIKE (`%`, `_`) and quoted for the PostgREST filter
  (`ilike_value`), so commas, brackets and quotes typed by a person stay text. Several words must all match; `"a phrase"`
  matches as written.
- **Same-name files get a number** ("Report (2).pdf") on upload, move and restore; a deliberate rename to a taken name is
  refused with a 409.
- **Free text stays free text** (comments, tags): they are searched, not forced into lists.
- **Stale edits are refused.** `PUT /api/documents/{id}` takes `expected_updated_at` (`app/concurrency.py`).

## Endpoints (`/api/documents`)

| | |
|---|---|
| `GET /overview` | Folders with file counts, library counts, filter options, `ams_missing`, `unindexed`. |
| `GET /search` | The one query behind every view: `q`, `folder_id`, `recursive`, `unfiled`, `trashed`, `types`, `exts`, `starred`, `added_from/to`, `min_size/max_size`, `tags`, `uploader`, `names_only`, `sort`, `dir`, `limit` (<= 500). Returns `{items, total, truncated, terms}`; an item found only by the words inside the file has `found_inside`. |
| `POST /upload` | multipart: `file`, `name`, `description`, `folder_id`, `tags`. Up to 100 MB. 409 for an identical file in the folder. Returns `also_in` when the same file is stored elsewhere. |
| `PUT /{id}` | rename, comment, star, tags, move (`folder_id`; null = Unfiled). |
| `POST /bulk` | `star unstar move tag_add tag_remove trash restore purge` on up to 200 files; reports `{done, failed[{id,name,error}]}`. |
| `DELETE /{id}` | to the trash (manager); `?permanent=true` from the trash. |
| `GET /{id}/activity` | history of one file. |
| `POST /reindex` | manager: read the words of files uploaded before text search (a few per call). |
| `POST/PUT/DELETE /folders...` | create, rename, move (no loops, depth <= 8), delete (empty only, manager). |
| `POST /folders/seed-ams` | manager: create the AMS structure. Idempotent. |

Until `supabase_migration_documents_v2.sql` has been run every endpoint answers 409 "needs its one-time upgrade" (a
database fault during that check is reported as a fault, not as "run the SQL").

## The AMS structure

`document_structure.py` holds the tree as data (85 folders: the root, sections 1 to 9, and their subfolders). The upgrade SQL
creates the root and the nine sections; **Set up the AMS folders** (manager) creates the rest. `plan_seed` is pure:
folders are recognised by `seed_key`, not name, so a renamed folder is not created again; a folder made by hand under the
same name (with or without the section number) is adopted with its files; pressing the button twice creates nothing the
second time. To change the structure, edit the list; new entries are created the next time it is pressed.

## Preview, open, print

Done in the browser (`frontend/app/documents/viewer/`). The storage server answers cross-origin reads
(`Access-Control-Allow-Origin: *`), so files are read into the page when needed. Images, audio, video and PDF use the browser;
Word (`.docx`, `docx-preview`) and spreadsheets (`xlsx`) are drawn in the page; older `.doc/.ppt/...` offer Microsoft's online
viewer only when asked (it receives the file's web address). Printing goes through a hidden frame. The Content-Security-Policy
(report-only) in `next.config.ts` allows the storage host for frames and media for this reason.

## Limits worth knowing

- Text search reads the first 30,000 characters of PDF, Word, Excel, PowerPoint and plain-text files up to 25 MB. Scanned
  pages and old binary formats are found by name, comment, tags and folder instead.
- A search returns at most 500 files; a cut-off answer says so with the real total.
- 100 MB per file; Supabase's own per-object limit may be lower on the free plan.

## Owner steps

1. Run `supabase_migration_documents_v2.sql` in the Supabase SQL editor **before** the new page is deployed.
2. Open Documents, press **Set up the AMS folders**, then **Read them now** (files added earlier become searchable by content).
3. Move files out of Unfiled and into folders as time allows.
