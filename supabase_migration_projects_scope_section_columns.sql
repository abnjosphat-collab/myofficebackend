-- Projects scope (Batch 4): a nullable `section` column on the tables that
-- never had one.
--
-- Tables: equipment, work_orders, breakdowns, notices, sop_documents,
-- services, daily_reports, maintenance_schedules.
--
-- Existing rows stay NULL, which means "not Projects": a scoped account sees
-- only rows stamped with its own section (or stamped later by triage). No
-- backfill runs here — guessing a section for old rows would misfile them.
--
-- Deliberately NOT included (see docs/PROJECTS_SCOPE.md Batch 4):
-- pachedu_reports / vfl_reports already carry section_choice (pattern
-- widening only, no new column); availabilities, compliance_register and
-- condition_monitoring inherit their equipment's section; corrective_actions,
-- findings and other child rows inherit their parent; compressors, photos,
-- drivers, production, contractors and failure-modes have no section link
-- and stay shared-readable.
--
-- BACKUP: this file only ADDs nullable columns (rollback is DROP COLUMN),
-- but snapshot the database before applying, as with any DDL.
--
-- APPLY BEFORE DEPLOYING the Batch 4 backend: unscoped callers never touch
-- the new columns, but a scoped list predicates on `section` and 500s until
-- its column exists. After applying, allow ~30s for the PostgREST schema
-- cache to pick the columns up before testing scoped lists.
--
-- Run in the Supabase SQL editor, top to bottom. NOT run by Muse — the
-- owner applies it.

BEGIN;

-- Guardrail 1: every target table must exist before any ALTER runs.
DO $$
DECLARE
  missing TEXT[];
BEGIN
  SELECT array_agg(t) INTO missing FROM (
    SELECT unnest(ARRAY[
      'equipment', 'work_orders', 'breakdowns', 'notices', 'sop_documents',
      'services', 'daily_reports', 'maintenance_schedules'
    ]) AS t
    WHERE NOT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = t
    )
  ) s;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'missing tables, refusing to continue: %', array_to_string(missing, ', ');
  END IF;
END
$$;

ALTER TABLE public.equipment ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.work_orders ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.breakdowns ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.notices ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.sop_documents ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.services ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.daily_reports ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE public.maintenance_schedules ADD COLUMN IF NOT EXISTS section TEXT;

COMMENT ON COLUMN public.equipment.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.work_orders.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts. to_section/from_section stay routing, not ownership.';
COMMENT ON COLUMN public.breakdowns.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.notices.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.sop_documents.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.services.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.daily_reports.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts.';
COMMENT ON COLUMN public.maintenance_schedules.section IS 'Owning section (e.g. Projects). NULL = untriaged, invisible to scoped accounts. Generated work orders inherit it.';

-- Guardrail 2: every column is present after the ALTERs.
DO $$
DECLARE
  missing TEXT[];
BEGIN
  SELECT array_agg(t) INTO missing FROM (
    SELECT unnest(ARRAY[
      'equipment', 'work_orders', 'breakdowns', 'notices', 'sop_documents',
      'services', 'daily_reports', 'maintenance_schedules'
    ]) AS t
    WHERE NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = t
        AND column_name = 'section'
    )
  ) s;
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'section column missing after ALTER, refusing to commit: %', array_to_string(missing, ', ');
  END IF;
END
$$;

COMMIT;

-- Verify (read-only): one row per new column, all nullable text.
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND column_name = 'section'
  AND table_name IN ('equipment', 'work_orders', 'breakdowns', 'notices',
    'sop_documents', 'services', 'daily_reports', 'maintenance_schedules')
ORDER BY table_name;
