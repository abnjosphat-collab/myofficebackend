-- Overtime may be performed by Engineering personnel but charged to another
-- department. Keep that accounting destination separate from `department`, which
-- identifies the employee's home department. Existing rows are Engineering costs.
-- Safe to re-run in the Supabase SQL editor.

ALTER TABLE overtime
  ADD COLUMN IF NOT EXISTS cost_centre TEXT NOT NULL DEFAULT 'Engineering';

UPDATE overtime
SET cost_centre = 'Engineering'
WHERE cost_centre IS NULL OR BTRIM(cost_centre) = '';
