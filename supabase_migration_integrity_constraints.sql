-- ============================================================
-- Data integrity: audit trail, approver columns, stale-edit column, real links and checks
-- File: backend/supabase_migration_integrity_constraints.sql
-- Date: 2026-10-19. Owner runs this in the Supabase SQL Editor. Safe to re-run (idempotent).
--
-- The application works BEFORE and AFTER this file is run: every feature that needs a column or a table from here
-- switches itself on as soon as it exists (and /api/health shows `audit: missing` until the audit table is created).
-- Run it, then check /api/health says `audit: ok`.
--
-- 1. audit_log        who changed what, and when (written by app/audit.py for every write to the payroll and people tables)
-- 2. overtime.updated_at (+ trigger)   lets the form refuse a save made from a stale copy
-- 3. leaves approver columns           who approved or rejected a leave, stamped by the server
-- 4. foreign keys (NOT VALID)          overtime, leaves and PPE must refer to a real employee; a renamed employee id carries
--                                       their history with it; an employee with records cannot be deleted. NOT VALID means
--                                       the rule applies to every new and changed row now; the old rows are checked when you
--                                       run the VALIDATE statements at the end, after the orphans below are resolved.
-- 5. check constraints (NOT VALID)     dates and times stored as text must be well-formed; hours in range; known statuses
--
-- Not in this file (they refuse to run until existing duplicates are resolved, so they are separate):
--   supabase_migration_overtime_no_duplicates.sql, supabase_migration_leaves_ppe_no_duplicates.sql
-- ============================================================

-- -- 1. audit_log --------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.audit_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at          timestamptz NOT NULL DEFAULT now(),
  actor_id    text,
  actor_email text NOT NULL DEFAULT 'system',
  actor_name  text,
  actor_role  text,
  action      text NOT NULL,          -- create | update | approve | reject | cancel | delete | upsert
  table_name  text NOT NULL,
  record_id   text,
  changes     jsonb,                  -- updates: the fields that changed, with their new values
  snapshot    jsonb                   -- creates and deletes: the whole row (a deleted record can always be put back)
);
CREATE INDEX IF NOT EXISTS audit_log_record_idx ON public.audit_log (table_name, record_id, at DESC);
CREATE INDEX IF NOT EXISTS audit_log_at_idx ON public.audit_log (at DESC);
ALTER TABLE public.audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.audit_log FROM anon, authenticated, PUBLIC;
GRANT ALL ON TABLE public.audit_log TO service_role;

-- -- 2. overtime.updated_at ---------------------------------------------------------------------
ALTER TABLE public.overtime ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();
UPDATE public.overtime SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL;
DROP TRIGGER IF EXISTS set_overtime_updated_at ON public.overtime;
CREATE TRIGGER set_overtime_updated_at BEFORE UPDATE ON public.overtime
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- -- 3. leaves: who approved or rejected -----------------------------------------------------------
ALTER TABLE public.leaves ADD COLUMN IF NOT EXISTS approved_by text;
ALTER TABLE public.leaves ADD COLUMN IF NOT EXISTS approved_at timestamptz;
ALTER TABLE public.leaves ADD COLUMN IF NOT EXISTS rejected_by text;
ALTER TABLE public.leaves ADD COLUMN IF NOT EXISTS rejected_at timestamptz;

-- -- 4. foreign keys (NOT VALID: enforced for new and changed rows; validate the old ones afterwards) ----
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['overtime', 'leaves', 'ppe_records'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_' || t || '_employee' AND conrelid = ('public.' || t)::regclass) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (employee_id) REFERENCES public.employees (employee_id) ON UPDATE CASCADE ON DELETE RESTRICT NOT VALID',
        t, 'fk_' || t || '_employee');
    END IF;
  END LOOP;
END $$;

-- -- 5. check constraints (NOT VALID) -------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'overtime_date_format') THEN
    ALTER TABLE public.overtime ADD CONSTRAINT overtime_date_format CHECK (date ~ '^\d{4}-\d{2}-\d{2}$') NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'overtime_time_format') THEN
    ALTER TABLE public.overtime ADD CONSTRAINT overtime_time_format CHECK (
      (start_time IS NULL OR start_time ~ '^\d{2}:\d{2}$') AND (end_time IS NULL OR end_time ~ '^\d{2}:\d{2}$')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'overtime_hours_range') THEN
    ALTER TABLE public.overtime ADD CONSTRAINT overtime_hours_range CHECK (hours IS NULL OR (hours > 0 AND hours <= 24)) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'overtime_status_known') THEN
    ALTER TABLE public.overtime ADD CONSTRAINT overtime_status_known CHECK (
      status IN ('pending', 'approved', 'rejected', 'paid', 'cancelled')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'leaves_dates_in_order') THEN
    ALTER TABLE public.leaves ADD CONSTRAINT leaves_dates_in_order CHECK (end_date >= start_date) NOT VALID;
  END IF;
END $$;

-- ============================================================
-- After running: GET /api/health should show "audit": "ok" once someone makes a change.
--
-- Then, when the orphaned and malformed rows below are resolved, make the rules cover the old rows too:
--
-- 1. Rows that would fail the foreign keys (an employee id that matches nobody):
--    SELECT 'overtime' AS tbl, id, employee_id FROM public.overtime o WHERE NOT EXISTS (SELECT 1 FROM public.employees e WHERE e.employee_id = o.employee_id)
--    UNION ALL SELECT 'leaves', id, employee_id FROM public.leaves l WHERE NOT EXISTS (SELECT 1 FROM public.employees e WHERE e.employee_id = l.employee_id)
--    UNION ALL SELECT 'ppe_records', id, employee_id FROM public.ppe_records p WHERE NOT EXISTS (SELECT 1 FROM public.employees e WHERE e.employee_id = p.employee_id);
--
-- 2. Then validate (each takes a moment and fails, naming the problem, if a bad row is left):
--    ALTER TABLE public.overtime    VALIDATE CONSTRAINT fk_overtime_employee;
--    ALTER TABLE public.leaves      VALIDATE CONSTRAINT fk_leaves_employee;
--    ALTER TABLE public.ppe_records VALIDATE CONSTRAINT fk_ppe_records_employee;
--    ALTER TABLE public.overtime    VALIDATE CONSTRAINT overtime_date_format;
--    ALTER TABLE public.overtime    VALIDATE CONSTRAINT overtime_time_format;
--    ALTER TABLE public.overtime    VALIDATE CONSTRAINT overtime_hours_range;
--    ALTER TABLE public.overtime    VALIDATE CONSTRAINT overtime_status_known;
--    ALTER TABLE public.leaves      VALIDATE CONSTRAINT leaves_dates_in_order;
--
-- Rollback (each is independent):
--    DROP TABLE public.audit_log;
--    DROP TRIGGER set_overtime_updated_at ON public.overtime;  ALTER TABLE public.overtime DROP COLUMN updated_at;
--    ALTER TABLE public.leaves DROP COLUMN approved_by, DROP COLUMN approved_at, DROP COLUMN rejected_by, DROP COLUMN rejected_at;
--    ALTER TABLE public.overtime DROP CONSTRAINT fk_overtime_employee;  (likewise fk_leaves_employee, fk_ppe_records_employee)
--    ALTER TABLE public.overtime DROP CONSTRAINT overtime_date_format;  (likewise the other checks)
-- ============================================================
