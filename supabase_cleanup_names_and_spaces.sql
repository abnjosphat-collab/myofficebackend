-- ============================================================
-- One-off tidy: stray spaces in names
-- File: backend/supabase_cleanup_names_and_spaces.sql
-- Date: 2026-10-19. Owner runs this in the Supabase SQL Editor. This CHANGES DATA (names only), so read section 1 first.
-- Safe to re-run: once the names are clean it changes nothing.
--
-- Why: 31 employees (checked 2026-10-19) have a leading, trailing or doubled space in their name ("Tafadzwa ", "Stanley  Musevenzo"),
-- and the same person's name is copied onto their overtime, leave and PPE records. Names that differ by a space are different
-- strings to every search and match. The application now tidies names as they are saved; this fixes what is already stored.
-- Nothing here changes an employee_id, a date or an amount.
-- ============================================================

-- 1. How many rows each statement below would change (read-only)
SELECT 'employees' AS tbl, count(*) AS rows_to_tidy FROM public.employees
  WHERE first_name <> btrim(regexp_replace(first_name, '\s+', ' ', 'g')) OR last_name <> btrim(regexp_replace(last_name, '\s+', ' ', 'g'))
UNION ALL SELECT 'overtime', count(*) FROM public.overtime WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'))
UNION ALL SELECT 'leaves', count(*) FROM public.leaves WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'))
UNION ALL SELECT 'ppe_records', count(*) FROM public.ppe_records WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'));

-- 2. Tidy
UPDATE public.employees
  SET first_name = btrim(regexp_replace(first_name, '\s+', ' ', 'g')), last_name = btrim(regexp_replace(last_name, '\s+', ' ', 'g'))
  WHERE first_name <> btrim(regexp_replace(first_name, '\s+', ' ', 'g')) OR last_name <> btrim(regexp_replace(last_name, '\s+', ' ', 'g'));

UPDATE public.overtime SET employee_name = btrim(regexp_replace(employee_name, '\s+', ' ', 'g'))
  WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'));
UPDATE public.leaves SET employee_name = btrim(regexp_replace(employee_name, '\s+', ' ', 'g'))
  WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'));
UPDATE public.ppe_records SET employee_name = btrim(regexp_replace(employee_name, '\s+', ' ', 'g'))
  WHERE employee_name <> btrim(regexp_replace(employee_name, '\s+', ' ', 'g'));
