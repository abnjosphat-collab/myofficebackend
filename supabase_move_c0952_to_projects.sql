-- Projects scope (Batch 7): move employee C0952 (Takudzwa Chibi) into the
-- Projects section.
--
-- Effect: every person-linked row that resolves through the employee's
-- CURRENT section (leaves, NEC/artisan timesheets, PPE, training,
-- competency, standby/duty, stock issues by recipient) becomes visible to
-- the Projects-scoped account, and only there. Direct-section records he
-- may have filed (near miss, PTO, etc.) stay in their own sections:
-- authorship was never ownership in this design, and this file moves
-- exactly one row — the employee — nothing else.
--
-- This does NOT set his account's section limit: that is done in the app
-- (Admin > Manage user > section-limit, or PATCH
-- /api/admin/users/{id}/scope). Apply order for go-live: Batch 1 user-scope
-- SQL, Batch 4 section-columns SQL, this file, then deploy.
--
-- BACKUP: the pre-move row is copied into
-- public.backup_employees_projects_scope (created here, kept afterwards)
-- before the UPDATE runs. Rollback is one UPDATE from that table (see the
-- commented statement at the bottom).
--
-- Re-runnable: if C0952 is already in Projects the file reports so and
-- changes nothing. Run in the Supabase SQL editor, top to bottom. NOT run
-- by Muse — the owner applies it.

BEGIN;

-- Guardrail 1: the table and the two columns this file touches must exist.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'employees'
  ) THEN
    RAISE EXCEPTION 'public.employees does not exist, refusing to continue';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'employees'
      AND column_name = 'employee_id'
  ) THEN
    RAISE EXCEPTION 'employees.employee_id does not exist, refusing to continue';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'employees'
      AND column_name = 'section'
  ) THEN
    RAISE EXCEPTION 'employees.section does not exist, refusing to continue';
  END IF;
  IF (
    SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'employees'
      AND column_name IN ('first_name', 'last_name')
  ) != 2 THEN
    RAISE EXCEPTION 'employees.first_name/last_name do not both exist, refusing to continue';
  END IF;
END
$$;

-- Guardrail 2: exactly one C0952 row must exist, and we show who it is
-- BEFORE anything changes. Check the name below matches Takudzwa Chibi.
DO $$
DECLARE
  found INT;
  who TEXT;
  current_section TEXT;
BEGIN
  SELECT count(*), max(first_name || ' ' || last_name), max(section)
    INTO found, who, current_section
    FROM public.employees
   WHERE employee_id = 'C0952';
  IF found = 0 THEN
    RAISE EXCEPTION 'no employees row with employee_id C0952, refusing to continue';
  END IF;
  IF found > 1 THEN
    RAISE EXCEPTION 'more than one employees row with employee_id C0952 (%), refusing to continue', found;
  END IF;
  RAISE NOTICE 'moving % (C0952) from section % to Projects', who, current_section;
END
$$;

-- Backup: keep the pre-move image. Created once; a re-run never
-- overwrites the original backup row.
CREATE TABLE IF NOT EXISTS public.backup_employees_projects_scope (
  employee_id TEXT PRIMARY KEY,
  section TEXT,
  first_name TEXT,
  last_name TEXT,
  backed_up_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.backup_employees_projects_scope
  (employee_id, section, first_name, last_name)
SELECT employee_id, section, first_name, last_name
  FROM public.employees
 WHERE employee_id = 'C0952'
ON CONFLICT (employee_id) DO NOTHING;

-- The move (no-op with a notice when already in Projects).
DO $$
DECLARE
  moved INT;
BEGIN
  UPDATE public.employees
     SET section = 'Projects'
   WHERE employee_id = 'C0952'
     AND section IS DISTINCT FROM 'Projects';
  GET DIAGNOSTICS moved = ROW_COUNT;
  IF moved = 0 THEN
    RAISE NOTICE 'C0952 is already in Projects: nothing changed';
  END IF;
END
$$;

-- Guardrail 3: C0952 reads back in Projects, still exactly one row.
DO $$
DECLARE
  found INT;
  current_section TEXT;
BEGIN
  SELECT count(*), max(section) INTO found, current_section
    FROM public.employees
   WHERE employee_id = 'C0952';
  IF found != 1 OR current_section IS DISTINCT FROM 'Projects' THEN
    RAISE EXCEPTION 'C0952 did not land in Projects (rows %, section %), refusing to commit', found, current_section;
  END IF;
END
$$;

COMMIT;

-- Verify (read-only): the moved row plus its backup pre-image.
SELECT employee_id, first_name, last_name, section
  FROM public.employees
 WHERE employee_id = 'C0952';
SELECT employee_id, section AS backed_up_section, backed_up_at
  FROM public.backup_employees_projects_scope
 WHERE employee_id = 'C0952';

-- Rollback (run by hand only, never automatically):
-- UPDATE public.employees e
--    SET section = b.section
--   FROM public.backup_employees_projects_scope b
--  WHERE e.employee_id = b.employee_id
--    AND e.employee_id = 'C0952';
