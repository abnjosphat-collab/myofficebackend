-- Artisan Timesheets v2 — signature columns, drop adjustment.
-- Run in Supabase SQL editor after the initial artisan_timesheets migration.

ALTER TABLE artisan_timesheets
  ADD COLUMN IF NOT EXISTS compiled_by_signature text,
  ADD COLUMN IF NOT EXISTS approved_electrical_foreman_signature text,
  ADD COLUMN IF NOT EXISTS approved_mechanical_foreman_signature text,
  ADD COLUMN IF NOT EXISTS authorized_by_signature text;

ALTER TABLE artisan_timesheets DROP COLUMN IF EXISTS adjustment;

NOTIFY pgrst, 'reload schema';
