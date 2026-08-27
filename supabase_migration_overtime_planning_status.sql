-- New overtime entries can be tagged Planned or Unplanned, feeding the
-- Weekly Summary's planned/unplanned hour breakdown. Nullable — existing
-- records stay NULL ("unclassified") rather than being backfilled with a
-- guessed value. Run once in the Supabase SQL editor. Safe to re-run
-- (IF NOT EXISTS is idempotent).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS planning_status TEXT;
