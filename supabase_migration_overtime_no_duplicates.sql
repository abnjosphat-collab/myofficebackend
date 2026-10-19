-- ============================================================
-- Overtime: the database refuses an exact duplicate (same person, date and times)
-- File: backend/supabase_migration_overtime_no_duplicates.sql
-- Date: 2026-10-19. Owner runs this in the Supabase SQL Editor AFTER the existing duplicates below are resolved.
--
-- Why: the backend now refuses a new or moved overtime request whose time overlaps one the person already has
-- (app/overtime_conflicts.py, HTTP 409). That check reads, then writes, so two laptops submitting in the same
-- instant could still both get through. This index closes that gap for exact duplicates, where the database
-- itself is the last word. Overlaps that are not identical are still caught by the backend check.
--
-- Existing data: 19 groups of identical live requests already exist (checked 2026-10-19; for example the same
-- person, date and hours entered two or three times and approved each time). The index cannot be created while
-- they exist, so this file stops with a clear message instead of failing half way. Review each group with the
-- query in section 1, keep one request, and set the others to 'cancelled' (or delete them) before running section 2.
-- Do not delete approved requests blindly: check payroll first, because an approved duplicate may already have
-- been counted.
-- Idempotent: safe to re-run.
-- ============================================================

-- 1. Review (read-only): every group of identical live requests, oldest first.
SELECT employee_id, employee_name, date, start_time, end_time,
       count(*) AS copies,
       array_agg(id ORDER BY id) AS request_ids,
       array_agg(status ORDER BY id) AS statuses
FROM public.overtime
WHERE start_time IS NOT NULL AND end_time IS NOT NULL
  AND coalesce(status, '') NOT IN ('rejected', 'cancelled')
GROUP BY employee_id, employee_name, date, start_time, end_time
HAVING count(*) > 1
ORDER BY date DESC;

-- 2. The guard. Refuses to run while duplicates exist.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.overtime
    WHERE start_time IS NOT NULL AND end_time IS NOT NULL
      AND coalesce(status, '') NOT IN ('rejected', 'cancelled')
    GROUP BY employee_id, date, start_time, end_time
    HAVING count(*) > 1
  ) THEN
    RAISE EXCEPTION 'Identical live overtime requests still exist. Resolve them (section 1) and run this again.';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS overtime_one_live_request_per_slot
  ON public.overtime (employee_id, date, start_time, end_time)
  WHERE start_time IS NOT NULL AND end_time IS NOT NULL
    AND coalesce(status, '') NOT IN ('rejected', 'cancelled');

-- Rollback:
--   DROP INDEX IF EXISTS public.overtime_one_live_request_per_slot;
