-- Training register details round 2 (REVIEW FILE — do NOT run without owner approval).
--
-- Prerequisites:
--   1. supabase_migration_training_employee_link.sql and
--      supabase_migration_training_optional_expiry.sql have been applied.
--   2. Owner confirms a maintenance window; this only ADDs nullable columns
--      plus one backfill UPDATE, so reads/writes keep working before and after.
--   3. Verify against an isolated test database first (production is over quota).
--
-- What this does:
--   - start_date / completion_date: the training window split. completion_date
--     takes over the duplicate-guard identity from training_date.
--   - grade: optional pass level (Distinction, Merit, Pass, Fail), free choice
--     enforced by the API, stored as plain text.
--   - details: optional free text for the specific skills trained on.
--   - trainers: optional free text for the trainers' names.
--   - venue: optional free text for where the training happened.
--   - Backfill: completion_date = training_date wherever a training date was
--     recorded, so existing rows keep their duplicate identity and history.
--
-- What this deliberately does NOT do:
--   - No UPDATE of existing display values, no dedupe DELETE, no removal of
--     the legacy training_date column (kept for history; the API prefers
--     completion_date with a training_date fallback).
--
-- Verification (isolated test database, never production):
--   1. Apply this file, then GET /api/training — old rows list unchanged, with
--      completion_date equal to their old training_date.
--   2. POST one certification with start_date + completion_date + grade — expect
--      success; POST the same person+certification+completion_date again —
--      expect 409; POST the same with a different completion_date — expect
--      success (renewal).
--   3. POST with start_date after completion_date — expect 422.
--   4. POST with grade 'First Class' — expect 422.

alter table if exists public.training_certifications
  add column if not exists start_date date,
  add column if not exists completion_date date,
  add column if not exists grade text not null default '',
  add column if not exists details text not null default '',
  add column if not exists trainers text not null default '',
  add column if not exists venue text not null default '';

update public.training_certifications
  set completion_date = training_date
  where completion_date is null and training_date is not null;

create index if not exists idx_training_certifications_completion
  on public.training_certifications (completion_date);
create index if not exists idx_training_certifications_employee_completion
  on public.training_certifications (employee_id, completion_date);
