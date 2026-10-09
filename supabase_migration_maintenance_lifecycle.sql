-- supabase_migration_maintenance_lifecycle.sql
--
-- Maintenance workflow, slice 4: lifecycle facts and the permit checklist on a work order.
-- Plan: docs/plans/maintenance-workflow.md (myofficefrontend), sections M.2 (permits, lifecycle columns) and M.4.
--
-- NOT APPLIED. Run by the owner in the Supabase SQL editor, AFTER supabase_migration_maintenance_audit.sql, then:
--   python scripts/track_migration.py --mark-applied supabase_migration_maintenance_lifecycle.sql
--
-- Properties: additive (seven nullable or defaulted columns on work_orders), safe to run twice, no existing reader or
-- writer is touched. Rehearsed on a throwaway local PostgreSQL 16 with scripts/test_maintenance_lifecycle_sql.sh.
--
-- Order of rollout: this file, then the backend, then the frontend. The backend tolerates the file not being applied:
-- status moves still work and simply do not record the extra facts; saving permits fails until it is applied.

ALTER TABLE public.work_orders
  ADD COLUMN IF NOT EXISTS permits           jsonb       NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS started_at        timestamptz,
  ADD COLUMN IF NOT EXISTS completed_at      timestamptz,
  ADD COLUMN IF NOT EXISTS artisan_signed_by text,
  ADD COLUMN IF NOT EXISTS artisan_signed_at timestamptz,
  ADD COLUMN IF NOT EXISTS foreman_signed_by text,
  ADD COLUMN IF NOT EXISTS foreman_signed_at timestamptz;

-- permits is an object: {permit_key: {required, reference, label?}}. The keys are fixed by the backend.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'work_orders_permits_is_object') THEN
    ALTER TABLE public.work_orders ADD CONSTRAINT work_orders_permits_is_object CHECK (jsonb_typeof(permits) = 'object');
  END IF;
END $$;

NOTIFY pgrst, 'reload schema';

-- Read-only checks to run afterwards:
--   select column_name from information_schema.columns where table_name='work_orders'
--     and column_name in ('permits','started_at','completed_at','artisan_signed_by','artisan_signed_at','foreman_signed_by','foreman_signed_at');   -- 7 rows
--   select count(*) from public.work_orders;   -- unchanged from before

-- ROLLBACK (last resort; loses the permit references and signing facts entered since). Commented on purpose.
-- ALTER TABLE public.work_orders DROP CONSTRAINT IF EXISTS work_orders_permits_is_object;
-- ALTER TABLE public.work_orders
--   DROP COLUMN IF EXISTS foreman_signed_at, DROP COLUMN IF EXISTS foreman_signed_by,
--   DROP COLUMN IF EXISTS artisan_signed_at, DROP COLUMN IF EXISTS artisan_signed_by,
--   DROP COLUMN IF EXISTS completed_at, DROP COLUMN IF EXISTS started_at, DROP COLUMN IF EXISTS permits;
-- DELETE FROM schema_migrations WHERE filename = 'supabase_migration_maintenance_lifecycle.sql';
