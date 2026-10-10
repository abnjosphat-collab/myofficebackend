-- supabase_migration_maintenance_tools.sql
--
-- Maintenance workflow, slice 3: the tools a work order needs, and an index for the leave check.
-- Plan: docs/plans/maintenance-modules.md (myofficefrontend), sections 3 and 4.
--
-- NOT APPLIED. Run by the owner in the Supabase SQL editor, AFTER supabase_migration_maintenance_audit.sql, then:
--   python scripts/track_migration.py --mark-applied supabase_migration_maintenance_tools.sql
--
-- Properties: additive (one new table, one index), safe to run twice, no existing reader or writer is touched.
-- Rehearsed on a throwaway local PostgreSQL 16 with scripts/test_maintenance_tools_sql.sh, never against Supabase.
--
-- Check first (read-only). The foreign key assumes work_orders.id is integer or bigint:
--   select data_type from information_schema.columns where table_name='work_orders' and column_name='id';
--
-- Order of rollout: this file, then the backend, then the frontend. Until it is applied, the tools list on a work
-- order shows a failure (never an empty list); the pickers and the leave rule do not depend on it.

-- Tools needed for a job. tool_register_number is the key in the Tools register, deliberately not a foreign key:
-- that register is a separate workspace with text ids. tool_name is a snapshot so the job still reads correctly
-- if the tool is later renamed or archived. A null number is a free-text tool that is not in the register.
-- Maintenance records the need only; custody stays in /tools.
CREATE TABLE IF NOT EXISTS public.work_order_tools (
  id                   bigserial   PRIMARY KEY,
  work_order_id        bigint      NOT NULL REFERENCES public.work_orders(id) ON DELETE CASCADE,
  tool_register_number text,
  tool_name            text        NOT NULL CHECK (length(btrim(tool_name)) > 0),
  note                 text,
  added_by             uuid,
  created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_work_order_tools_wo ON public.work_order_tools (work_order_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_work_order_tools_register
  ON public.work_order_tools (work_order_id, tool_register_number) WHERE tool_register_number IS NOT NULL;
ALTER TABLE public.work_order_tools ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Authenticated read work order tools" ON public.work_order_tools;
CREATE POLICY "Authenticated read work order tools"
  ON public.work_order_tools FOR SELECT USING (auth.role() = 'authenticated');
-- No write policy: only the FastAPI backend (service role, bypasses RLS) writes.

-- The leave check reads approved leaves that cover a day.
CREATE INDEX IF NOT EXISTS idx_leaves_status_dates ON public.leaves (status, start_date, end_date);

NOTIFY pgrst, 'reload schema';

-- Read-only checks to run afterwards:
--   select count(*) from public.work_order_tools;   -- 0
--   select indexname from pg_indexes where indexname in ('idx_work_order_tools_wo','uq_work_order_tools_register','idx_leaves_status_dates');   -- 3 rows

-- ROLLBACK (last resort; loses the tools lists entered since). Commented on purpose.
-- DROP INDEX IF EXISTS public.idx_leaves_status_dates;
-- DROP TABLE IF EXISTS public.work_order_tools;
-- DELETE FROM schema_migrations WHERE filename = 'supabase_migration_maintenance_tools.sql';
