-- Hard link from a transferred lieu leave back to the overtime record it pays
-- off. One overtime record transfers at most once: the unique index rejects a
-- second leave with the same overtime_id, while ordinary leaves (NULL) never
-- collide. Run once in the Supabase SQL editor AFTER deploying the backend
-- that writes and reads overtime_id. Safe to re-run (IF NOT EXISTS guards).

ALTER TABLE leaves ADD COLUMN IF NOT EXISTS overtime_id integer;
CREATE UNIQUE INDEX IF NOT EXISTS leaves_overtime_id_uidx ON leaves(overtime_id);
