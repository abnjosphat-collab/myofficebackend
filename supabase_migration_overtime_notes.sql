-- The overtime form has always sent `notes`, but the `overtime` table never had a
-- matching column (same bug class as `department`). Without it, inserts that
-- include notes fail with PGRST204 ("Could not find the 'notes' column of
-- 'overtime' in the schema cache"). Run once in the Supabase SQL editor BEFORE
-- deploying the backend change that declares the field. Safe to re-run
-- (IF NOT EXISTS is idempotent).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS notes TEXT;
