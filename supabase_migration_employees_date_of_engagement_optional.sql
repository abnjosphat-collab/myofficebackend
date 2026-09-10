-- Allow personnel records without an engagement date (optional in MyOffice UI/API).
-- Run in Supabase Dashboard → SQL Editor (or apply via Supabase CLI / MCP migration).

ALTER TABLE public.employees
  ALTER COLUMN date_of_engagement DROP NOT NULL;
