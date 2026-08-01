-- Adds the night shift allowance flag (mirrors standby_allowance: a flat +8h
-- bonus paid once per contiguous run of days it's set on, computed client-side
-- — this column just stores the toggle). Run once in the Supabase SQL editor,
-- after supabase_migration_timesheets.sql.

ALTER TABLE public.timesheets ADD COLUMN IF NOT EXISTS nightshift_allowance BOOLEAN DEFAULT FALSE;
