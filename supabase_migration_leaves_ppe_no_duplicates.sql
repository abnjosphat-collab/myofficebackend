-- ============================================================
-- Leaves and PPE: the database refuses an exact duplicate
-- File: backend/supabase_migration_leaves_ppe_no_duplicates.sql
-- Date: 2026-10-19. Owner runs this AFTER the existing duplicates are resolved (see supabase_migration_overtime_no_duplicates.sql
-- for the same idea applied to overtime). Safe to re-run.
--
-- Why: the backend refuses a new or moved leave that shares a day with one the person already has, and a PPE record that
-- repeats an issue (same person, kind and date). Those checks read, then write, so two laptops submitting in the same
-- instant could both get through; these indexes make the database the last word for the exact-duplicate case.
--
-- Existing data (checked 2026-10-19): 7 groups of identical live leave requests and 9 groups of identical PPE issues already
-- exist, so the guards below stop with a clear message instead of failing half way. Review with the queries in section 1, keep
-- one record of each group, reject (leaves) or delete (PPE) the others, then run section 2. An approved duplicate may already
-- have been counted, so check before removing it.
-- ============================================================

-- 1. Review (read-only)
SELECT 'leaves' AS tbl, employee_id, start_date::text AS a, end_date::text AS b, count(*) AS copies, array_agg(id ORDER BY id) AS ids, array_agg(status ORDER BY id) AS statuses
FROM public.leaves WHERE status <> 'rejected'
GROUP BY employee_id, start_date, end_date HAVING count(*) > 1
UNION ALL
SELECT 'ppe_records', employee_id, ppe_type, issue_date::text, count(*), array_agg(id ORDER BY id), array_agg(status ORDER BY id)
FROM public.ppe_records WHERE status NOT IN ('returned', 'replaced', 'lost', 'disposed', 'cancelled')
GROUP BY employee_id, ppe_type, issue_date HAVING count(*) > 1
ORDER BY 1, 2;

-- 2. The guards (refuse to run while duplicates exist)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.leaves WHERE status <> 'rejected' GROUP BY employee_id, start_date, end_date HAVING count(*) > 1) THEN
    RAISE EXCEPTION 'Identical live leave requests still exist. Resolve them (section 1) and run this again.';
  END IF;
  IF EXISTS (SELECT 1 FROM public.ppe_records WHERE status NOT IN ('returned', 'replaced', 'lost', 'disposed', 'cancelled') GROUP BY employee_id, ppe_type, issue_date HAVING count(*) > 1) THEN
    RAISE EXCEPTION 'Identical PPE issues still exist. Resolve them (section 1) and run this again.';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS leaves_one_live_request_per_range
  ON public.leaves (employee_id, start_date, end_date) WHERE status <> 'rejected';

CREATE UNIQUE INDEX IF NOT EXISTS ppe_one_record_per_issue
  ON public.ppe_records (employee_id, ppe_type, issue_date) WHERE status NOT IN ('returned', 'replaced', 'lost', 'disposed', 'cancelled');

-- Rollback:
--   DROP INDEX IF EXISTS public.leaves_one_live_request_per_range;
--   DROP INDEX IF EXISTS public.ppe_one_record_per_issue;
