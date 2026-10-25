-- P2: set employee C0952 (Takudzwa Chibi) department to Engineering.
--
-- Verified 25 Oct 2026 (read-only): exactly one employees row with
-- employee_id C0952, department blank (''), section already 'Projects'.
--
-- BACKUP: the pre-fix department is copied into
-- public.backup_employees_c0952_department (created here, kept afterwards)
-- before the UPDATE runs. Rollback is one UPDATE from that table (see the
-- commented statement at the bottom).
--
-- Re-runnable: if C0952 department is already Engineering the file reports
-- so and changes nothing. Run in the Supabase SQL editor, top to bottom.
-- NOT run by Muse — the owner applies it.

BEGIN;

-- Guardrail 1: the table and the columns this file touches must exist.
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
      AND column_name = 'department'
  ) THEN
    RAISE EXCEPTION 'employees.department does not exist, refusing to continue';
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

-- Guardrail 2: exactly one C0952 row must exist in section Projects, and we
-- show who it is BEFORE anything changes. Check the name below matches
-- Takudzwa Chibi.
DO $$
DECLARE
  found INT;
  who TEXT;
  current_department TEXT;
  current_section TEXT;
BEGIN
  SELECT count(*), max(first_name || ' ' || last_name), max(department), max(section)
    INTO found, who, current_department, current_section
    FROM public.employees
   WHERE employee_id = 'C0952';
  IF found = 0 THEN
    RAISE EXCEPTION 'no employees row with employee_id C0952, refusing to continue';
  END IF;
  IF found > 1 THEN
    RAISE EXCEPTION 'more than one employees row with employee_id C0952 (%), refusing to continue', found;
  END IF;
  IF current_section IS DISTINCT FROM 'Projects' THEN
    RAISE EXCEPTION 'C0952 section is % (expected Projects), refusing to continue', current_section;
  END IF;
  RAISE NOTICE 'fixing % (C0952) department % to Engineering', who, coalesce(nullif(current_department, ''), '(blank)');
END
$$;

-- Backup: keep the pre-fix image. Created once; a re-run never
-- overwrites the original backup row.
CREATE TABLE IF NOT EXISTS public.backup_employees_c0952_department (
  employee_id TEXT PRIMARY KEY,
  department TEXT,
  section TEXT,
  backed_up_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.backup_employees_c0952_department
  (employee_id, department, section)
SELECT employee_id, department, section
  FROM public.employees
 WHERE employee_id = 'C0952'
ON CONFLICT (employee_id) DO NOTHING;

-- The fix (no-op with a notice when already Engineering; refuses when the
-- department holds any other value, since that breaks the blank premise).
DO $$
DECLARE
  fixed INT;
  current_department TEXT;
BEGIN
  UPDATE public.employees
     SET department = 'Engineering'
   WHERE employee_id = 'C0952'
     AND coalesce(department, '') = '';
  GET DIAGNOSTICS fixed = ROW_COUNT;
  IF fixed = 0 THEN
    SELECT department INTO current_department
      FROM public.employees
     WHERE employee_id = 'C0952';
    IF current_department IS DISTINCT FROM 'Engineering' THEN
      RAISE EXCEPTION 'C0952 department is % (not blank, not Engineering), refusing to continue', current_department;
    END IF;
    RAISE NOTICE 'C0952 department is already Engineering: nothing changed';
  END IF;
END
$$;

-- Guardrail 3: C0952 reads back in Engineering, still exactly one row.
DO $$
DECLARE
  found INT;
  current_department TEXT;
BEGIN
  SELECT count(*), max(department) INTO found, current_department
    FROM public.employees
   WHERE employee_id = 'C0952';
  IF found != 1 OR current_department IS DISTINCT FROM 'Engineering' THEN
    RAISE EXCEPTION 'C0952 did not land in Engineering (rows %, department %), refusing to commit', found, current_department;
  END IF;
END
$$;

COMMIT;

-- Verify (read-only): the fixed row plus its backup pre-image.
SELECT employee_id, first_name, last_name, department, section
  FROM public.employees
 WHERE employee_id = 'C0952';
SELECT employee_id, department AS backed_up_department, backed_up_at
  FROM public.backup_employees_c0952_department
 WHERE employee_id = 'C0952';

-- Rollback (run by hand only, never automatically):
-- UPDATE public.employees e
--    SET department = b.department
--   FROM public.backup_employees_c0952_department b
--  WHERE e.employee_id = b.employee_id
--    AND e.employee_id = 'C0952';
