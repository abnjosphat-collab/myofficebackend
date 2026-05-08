-- =====================================================================
-- EMPLOYEES TABLE — in-place fix (SAFE — no column drops)
-- Run this in: Supabase Dashboard → SQL Editor
--
-- NO table renames. Works directly on the existing "employees" table.
-- What this does:
--   1. Removes the trigger that auto-overwrites employee_id with UUID gibberish
--   2. Copies the clean EMP_XXXX values into employee_id (replaces gibberish)
--   3. Removes the auto-default so employee_id is a plain editable field
--   4. Adds a UNIQUE constraint on employee_id
--   5. Fixes the id column auto-increment sequence
--
-- NOTE: employee_id_string, leaves, ppe_due columns are intentionally
--       NOT dropped here because Supabase reports they are referenced
--       by other tables/views. Drop them separately after verifying.
-- =====================================================================

-- ── 1. Drop every trigger on employees ──────────────────────────────
DO $$
DECLARE
  rec RECORD;
BEGIN
  FOR rec IN
    SELECT trigger_name
    FROM information_schema.triggers
    WHERE event_object_table = 'employees'
      AND trigger_schema     = 'public'
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.employees', rec.trigger_name);
    RAISE NOTICE 'Dropped trigger: %', rec.trigger_name;
  END LOOP;
END $$;

-- ── 2. Replace UUID garbage with the clean EMP_XXXX values ──────────
UPDATE employees
SET   employee_id = employee_id_string
WHERE employee_id_string IS NOT NULL
  AND employee_id_string <> '';

-- ── 3. Drop the auto-default on employee_id (if any) ────────────────
ALTER TABLE employees
  ALTER COLUMN employee_id DROP DEFAULT;

-- ── 4. Add UNIQUE constraint so no two employees share an ID ─────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema    = 'public'
      AND table_name      = 'employees'
      AND constraint_type = 'UNIQUE'
      AND constraint_name = 'employees_employee_id_key'
  ) THEN
    ALTER TABLE employees
      ADD CONSTRAINT employees_employee_id_key UNIQUE (employee_id);
    RAISE NOTICE 'UNIQUE constraint added on employee_id';
  ELSE
    RAISE NOTICE 'UNIQUE constraint already exists — skipped';
  END IF;
END $$;

-- ── 5. Fix the id column so it auto-increments on INSERT ────────────
-- (Fixes: "null value in column id violates not-null constraint")
DO $$
BEGIN
  -- Create a sequence if it doesn't exist
  IF NOT EXISTS (SELECT 1 FROM pg_sequences WHERE schemaname='public' AND sequencename='employees_id_seq') THEN
    CREATE SEQUENCE public.employees_id_seq;
    RAISE NOTICE 'Created sequence employees_id_seq';
  END IF;
  -- Advance the sequence past the current max id so no collisions
  PERFORM setval('public.employees_id_seq', COALESCE((SELECT MAX(id) FROM employees), 0) + 1, false);
  -- Set the column default to use the sequence
  ALTER TABLE employees ALTER COLUMN id SET DEFAULT nextval('public.employees_id_seq');
  -- Link the sequence to the column (so DROP TABLE drops it too)
  ALTER SEQUENCE public.employees_id_seq OWNED BY employees.id;
  RAISE NOTICE 'id column now auto-increments from sequence';
END $$;

-- ── 6. Verify ────────────────────────────────────────────────────────
SELECT column_name, data_type, column_default, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public' AND table_name = 'employees'
ORDER BY ordinal_position;

SELECT
  COUNT(*)                                        AS total,
  COUNT(DISTINCT employee_id)                     AS unique_ids,
  COUNT(*) FILTER (WHERE employee_id LIKE 'EMP_%') AS auto_style_remaining
FROM employees;
