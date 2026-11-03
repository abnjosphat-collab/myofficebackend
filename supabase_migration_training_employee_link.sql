-- Training employee-link + bulk-entry columns (REVIEW FILE — do NOT run without owner approval).
--
-- Prerequisites:
--   1. The base table exists (supabase_migration_training_certifications.sql has been applied).
--   2. Owner confirms a maintenance window: this only ADDs nullable columns + indexes,
--      so reads/writes keep working before and after; no backfill runs here.
--   3. Production Supabase is over quota: verify against an isolated test database first.
--
-- What this does:
--   - provider / training_date: the shared course details bulk entry captures once.
--   - snapshot_designation / snapshot_section / snapshot_department: the register values
--     frozen at training time. Later personnel edits never rewrite them.
--   - group_evidence_url: an optional shared attendance-sheet link. It is stored
--     separately and never presented as anyone's individual certificate.
--   - batch_id: the client-generated id of the bulk submission that created the row.
--
-- What this deliberately does NOT do:
--   - No UPDATE of existing rows: legacy rows keep their free-text values untouched.
--     Only exact personnel-number matches may be linked later, one record at a time
--     through the API (PUT with the register employee_id); ambiguous names stay unlinked.
--   - No dedupe DELETE: if the same person+certification+date was recorded twice by hand,
--     the owner resolves it after reviewing the register, not in this migration.
--
-- Verification (isolated test database, never production):
--   1. Apply this file, then GET /api/training — old rows still list, new fields read
--      as "" / null, status still computed from expiry_date.
--   2. POST one certification with training_date + provider for an exact register id —
--      snapshots equal the register; then change the register designation and GET again —
--      the training snapshot is unchanged.
--   3. POST the same person+certification+training_date again — expect 409; POST the same
--      certification with a different training_date — expect success (renewal).
--   4. POST /api/training/bulk with two employees where one employee_id is mistyped —
--      expect created=1, failed=1, and a retry with only the failed entry succeeds
--      without duplicating the first.

alter table if exists public.training_certifications
  add column if not exists provider text not null default '',
  add column if not exists training_date date,
  add column if not exists snapshot_designation text not null default '',
  add column if not exists snapshot_section text not null default '',
  add column if not exists snapshot_department text not null default '',
  add column if not exists group_evidence_url text,
  add column if not exists batch_id text;

create index if not exists idx_training_certifications_training_date
  on public.training_certifications (training_date);
create index if not exists idx_training_certifications_employee_training
  on public.training_certifications (employee_id, training_date);
create index if not exists idx_training_certifications_batch
  on public.training_certifications (batch_id);

-- Exact-match backfill CHECK (read-only; run by the owner to see what WOULD link):
-- select t.id, t.employee_id, t.employee_name
-- from public.training_certifications t
-- where t.employee_id not in (select employee_id from public.employees);
-- Every row this returns stays as-is. Only rows whose employee_id exactly matches a
-- register personnel number (case-insensitive) count as linked; the rest are legacy
-- free-text rows and must not be force-matched.
