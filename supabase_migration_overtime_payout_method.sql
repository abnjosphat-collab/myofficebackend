-- Marks an overtime entry as either 'cash' (will be paid, the normal case) or
-- 'lieu' (compensated with time off instead of pay — matches the existing
-- "Leave in Lieu of Overtime" leave type in the Leaves module; no hard link
-- between the two records, just a manual flag set by whoever reconciles it).
-- Named payout_method, not payment_status, so it can't be confused with the
-- overtime table's own unrelated `status` column (whose values include an
-- unrelated 'paid' — the approval-lifecycle status). Nullable — existing
-- records stay NULL ("unclassified") rather than being backfilled with a
-- guessed value. Run once in the Supabase SQL editor. Safe to re-run
-- (IF NOT EXISTS is idempotent).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS payout_method TEXT;
