-- The overtime form has always sent `department` (autofilled from the employee
-- picker), but the `overtime` table never had a matching column, so every insert
-- fails with PGRST204 ("Could not find the 'department' column of 'overtime' in
-- the schema cache"). Run once in the Supabase SQL editor. Safe to re-run
-- (IF NOT EXISTS is idempotent).

ALTER TABLE overtime ADD COLUMN IF NOT EXISTS department TEXT;
