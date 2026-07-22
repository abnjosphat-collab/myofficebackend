-- Overtime "pressed for time" fast path: reason/contact/exact times are no longer
-- mandatory, and an entry can carry hours directly instead of start/end times.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS / DROP NOT NULL
-- are idempotent).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS hours NUMERIC;

-- These were likely NOT NULL in the original table definition; the backend model no
-- longer requires them, so the column must allow NULL too, or every hours-only insert
-- (no reason, no times) will fail at the database layer regardless of the API accepting it.
ALTER TABLE overtime ALTER COLUMN start_time DROP NOT NULL;
ALTER TABLE overtime ALTER COLUMN end_time DROP NOT NULL;
ALTER TABLE overtime ALTER COLUMN reason DROP NOT NULL;
ALTER TABLE overtime ALTER COLUMN contact_number DROP NOT NULL;
