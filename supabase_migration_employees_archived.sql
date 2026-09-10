-- Add archived flag for personnel no longer on the active roster (e.g. Hoist Drivers).
-- Run in Supabase Dashboard → SQL Editor.

ALTER TABLE employees
  ADD COLUMN IF NOT EXISTS archived boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_employees_archived ON employees (archived);
