-- Training optional expiry (REVIEW FILE — do NOT run without owner approval).
--
-- Prerequisites:
--   1. supabase_migration_training_employee_link.sql has been applied.
--   2. Owner confirms a maintenance window; this only relaxes one NOT NULL
--      constraint, so reads/writes keep working before and after.
--   3. Verify against an isolated test database first (production is over quota).
--
-- What this does:
--   - Allows expiry_date to be NULL, meaning the training does not expire.
--     The API reads such rows as Valid; the page shows "Does not expire".
--
-- What this deliberately does NOT do:
--   - No UPDATE of existing rows: every existing row keeps its expiry date.
--     Only new or explicitly cleared records have NULL.
--
-- Verification (isolated test database, never production):
--   1. Apply this file, then GET /api/training — old rows unchanged.
--   2. POST one certification without expiry_date — expect success with
--      expiry null and status Valid.
--   3. POST the same person+certification+training_date again — expect 409
--      (the duplicate guard does not depend on expiry).

alter table if exists public.training_certifications
  alter column expiry_date drop not null;
