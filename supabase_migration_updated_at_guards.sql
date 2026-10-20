-- supabase_migration_updated_at_conflict_guards.sql
-- employees + stock_issues gain updated_at so the edit forms can refuse a save
-- made from a stale copy (the assert_unchanged 409 pattern from concurrency.py).
-- spares/requisitions already have the column (verified live 2026-10-20).
-- Applied 20 Oct 2026 via scripts/apply_migration.py with the owner's word;
-- both columns + triggers re-checked live (employees, stock_issues).
-- Rollback per table:
--   DROP TRIGGER set_<table>_updated_at ON public.<table>; ALTER TABLE public.<table> DROP COLUMN updated_at;

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

-- 1. employees.updated_at ------------------------------------------------------
ALTER TABLE public.employees ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();
UPDATE public.employees SET updated_at = now() WHERE updated_at IS NULL;
DROP TRIGGER IF EXISTS set_employees_updated_at ON public.employees;
CREATE TRIGGER set_employees_updated_at BEFORE UPDATE ON public.employees
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
--    DROP TRIGGER set_employees_updated_at ON public.employees;  ALTER TABLE public.employees DROP COLUMN updated_at;

-- 2. stock_issues.updated_at ---------------------------------------------------
ALTER TABLE public.stock_issues ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now();
UPDATE public.stock_issues SET updated_at = now() WHERE updated_at IS NULL;
DROP TRIGGER IF EXISTS set_stock_issues_updated_at ON public.stock_issues;
CREATE TRIGGER set_stock_issues_updated_at BEFORE UPDATE ON public.stock_issues
  FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
--    DROP TRIGGER set_stock_issues_updated_at ON public.stock_issues;  ALTER TABLE public.stock_issues DROP COLUMN updated_at;
