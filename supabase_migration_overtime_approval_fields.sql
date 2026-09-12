-- Approval audit fields for overtime sign-off (manager approve/reject + signature image).
-- Bulk approve sends approval_signature; without this column PostgREST returns PGRST204.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE overtime ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE overtime ADD COLUMN IF NOT EXISTS approval_signature TEXT;
ALTER TABLE overtime ADD COLUMN IF NOT EXISTS rejected_by TEXT;
ALTER TABLE overtime ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ;

SELECT 'overtime approval columns ready.' AS result;
