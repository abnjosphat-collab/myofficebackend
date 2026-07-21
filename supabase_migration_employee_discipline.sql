-- Adds the trade discipline field (mechanical / electrical) to employees, for the
-- Employees-page bulk-mark + filter + discipline-export feature.
-- Run once in the Supabase SQL editor. Safe to re-run (IF NOT EXISTS).

ALTER TABLE employees ADD COLUMN IF NOT EXISTS discipline TEXT;

-- Optional but recommended: constrain to the two known values (or NULL = unset).
-- Skip this if you might add more disciplines later and don't want a migration each time.
ALTER TABLE employees DROP CONSTRAINT IF EXISTS employees_discipline_check;
ALTER TABLE employees ADD CONSTRAINT employees_discipline_check
    CHECK (discipline IS NULL OR discipline IN ('mechanical', 'electrical'));

CREATE INDEX IF NOT EXISTS idx_employees_discipline ON employees(discipline);
