-- ============================================================
-- MyOffice security hardening 1/3 — enable RLS on exposed tables
-- File: backend/supabase_migration_enable_rls_public_tables.sql
-- Date: 2026-10-18. Owner runs this in the Supabase SQL Editor
-- (or scripts/apply_migration.py). NEVER run against a dev script
-- that writes elsewhere: target project is geomlsxphwaxpaloweky.
--
-- Problem: the 24 tables below exist in public with RLS OFF and anon
-- holding SELECT/INSERT/UPDATE/DELETE, so anyone with the publishable
-- key can read and write them over PostgREST without signing in.
--
-- Fix per table (idempotent, safe to re-run):
--   1. GRANT ALL TO service_role  — the backend keeps working: its
--      Supabase clients all use the service-role key (SUPABASE_KEY),
--      and service_role bypasses RLS.
--   2. REVOKE ALL FROM anon, authenticated, PUBLIC — close the hole
--      even for grants made via the Table Editor or via PUBLIC.
--   3. ALTER TABLE ... ENABLE ROW LEVEL SECURITY, with NO new
--      policies: RLS-on-with-no-policies denies every non-bypass
--      role, which is exactly what we want for backend-only tables.
--
-- Pre-change grep evidence (2026-10-18, recorded here per spec):
--   * Frontend Supabase .from(): ONLY user_profiles, 3 call sites,
--     all in frontend/lib/supabase.ts (lines 84, 93, 107:
--     upsertProfile/getProfile). Zero .from() on any table below.
--   * Frontend Supabase .rpc(): ZERO call sites anywhere under
--     frontend/app, frontend/lib, frontend/components, frontend/hooks.
--   * Backend anon-key use: exactly one place,
--     backend/app/routers/signatures.py _verify_password(), which
--     calls only throwaway.auth.sign_in_with_password() (GoTrue
--     password check) — no table or RPC access on any key.
--   * Every backend table/RPC call uses a service-role client:
--     the shared app/supabase_client.py singleton (SUPABASE_KEY),
--     or breakdowns.py / daily_reports.py module clients built
--     from the same SUPABASE_URL / SUPABASE_KEY pair.
-- Conclusion: enabling RLS + revoking anon/authenticated cannot
-- break the app; only direct PostgREST access with the anon key
-- (the exposure being closed) is affected.
--
-- Sequences are deliberately NOT touched: sequence USAGE alone
-- grants nothing readable, RLS already blocks the tables, and
-- revoking sequence rights risks breaking service-role inserts
-- under an unverifiable live grant topology.
--
-- Run order: this file FIRST, then
--   supabase_migration_lock_down_functions.sql,
-- then optionally supabase_migration_function_search_path.sql.
-- End-of-file verification SELECTs must each return zero rows.
-- ============================================================

-- ── Section A: the 24 exposed tables ─────────────────────────

-- employees
GRANT ALL ON TABLE public.employees TO service_role;
REVOKE ALL ON TABLE public.employees FROM anon, authenticated, PUBLIC;
ALTER TABLE public.employees ENABLE ROW LEVEL SECURITY;

-- leaves
GRANT ALL ON TABLE public.leaves TO service_role;
REVOKE ALL ON TABLE public.leaves FROM anon, authenticated, PUBLIC;
ALTER TABLE public.leaves ENABLE ROW LEVEL SECURITY;

-- overtime
GRANT ALL ON TABLE public.overtime TO service_role;
REVOKE ALL ON TABLE public.overtime FROM anon, authenticated, PUBLIC;
ALTER TABLE public.overtime ENABLE ROW LEVEL SECURITY;

-- duty_roster (repo-created: supabase_migration_duty_roster.sql)
GRANT ALL ON TABLE public.duty_roster TO service_role;
REVOKE ALL ON TABLE public.duty_roster FROM anon, authenticated, PUBLIC;
ALTER TABLE public.duty_roster ENABLE ROW LEVEL SECURITY;

-- duty_rotations (repo-created: supabase_migration_duty_rotations.sql)
GRANT ALL ON TABLE public.duty_rotations TO service_role;
REVOKE ALL ON TABLE public.duty_rotations FROM anon, authenticated, PUBLIC;
ALTER TABLE public.duty_rotations ENABLE ROW LEVEL SECURITY;

-- rotation_covers (repo-created: supabase_migration_rotation_covers.sql)
GRANT ALL ON TABLE public.rotation_covers TO service_role;
REVOKE ALL ON TABLE public.rotation_covers FROM anon, authenticated, PUBLIC;
ALTER TABLE public.rotation_covers ENABLE ROW LEVEL SECURITY;

-- standby_rotations (repo-created: supabase_migration_standby_rotations.sql)
GRANT ALL ON TABLE public.standby_rotations TO service_role;
REVOKE ALL ON TABLE public.standby_rotations FROM anon, authenticated, PUBLIC;
ALTER TABLE public.standby_rotations ENABLE ROW LEVEL SECURITY;

-- standby_schedules
GRANT ALL ON TABLE public.standby_schedules TO service_role;
REVOKE ALL ON TABLE public.standby_schedules FROM anon, authenticated, PUBLIC;
ALTER TABLE public.standby_schedules ENABLE ROW LEVEL SECURITY;

-- drivers
GRANT ALL ON TABLE public.drivers TO service_role;
REVOKE ALL ON TABLE public.drivers FROM anon, authenticated, PUBLIC;
ALTER TABLE public.drivers ENABLE ROW LEVEL SECURITY;

-- password_reset_requests (also carries its own ENABLE RLS in
-- supabase_migration_password_reset_requests.sql; re-running is a no-op)
GRANT ALL ON TABLE public.password_reset_requests TO service_role;
REVOKE ALL ON TABLE public.password_reset_requests FROM anon, authenticated, PUBLIC;
ALTER TABLE public.password_reset_requests ENABLE ROW LEVEL SECURITY;

-- work_orders
GRANT ALL ON TABLE public.work_orders TO service_role;
REVOKE ALL ON TABLE public.work_orders FROM anon, authenticated, PUBLIC;
ALTER TABLE public.work_orders ENABLE ROW LEVEL SECURITY;

-- breakdowns
GRANT ALL ON TABLE public.breakdowns TO service_role;
REVOKE ALL ON TABLE public.breakdowns FROM anon, authenticated, PUBLIC;
ALTER TABLE public.breakdowns ENABLE ROW LEVEL SECURITY;

-- daily_reports
GRANT ALL ON TABLE public.daily_reports TO service_role;
REVOKE ALL ON TABLE public.daily_reports FROM anon, authenticated, PUBLIC;
ALTER TABLE public.daily_reports ENABLE ROW LEVEL SECURITY;

-- sheq_reports
GRANT ALL ON TABLE public.sheq_reports TO service_role;
REVOKE ALL ON TABLE public.sheq_reports FROM anon, authenticated, PUBLIC;
ALTER TABLE public.sheq_reports ENABLE ROW LEVEL SECURITY;

-- sheq_findings (also drops the live over-broad policy below)
GRANT ALL ON TABLE public.sheq_findings TO service_role;
REVOKE ALL ON TABLE public.sheq_findings FROM anon, authenticated, PUBLIC;
ALTER TABLE public.sheq_findings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Enable all for authenticated users" ON public.sheq_findings;

-- sheq_inspections (also drops the live over-broad policy below)
GRANT ALL ON TABLE public.sheq_inspections TO service_role;
REVOKE ALL ON TABLE public.sheq_inspections FROM anon, authenticated, PUBLIC;
ALTER TABLE public.sheq_inspections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Enable all for authenticated users" ON public.sheq_inspections;

-- ppe_records
GRANT ALL ON TABLE public.ppe_records TO service_role;
REVOKE ALL ON TABLE public.ppe_records FROM anon, authenticated, PUBLIC;
ALTER TABLE public.ppe_records ENABLE ROW LEVEL SECURITY;

-- spare_parts
GRANT ALL ON TABLE public.spare_parts TO service_role;
REVOKE ALL ON TABLE public.spare_parts FROM anon, authenticated, PUBLIC;
ALTER TABLE public.spare_parts ENABLE ROW LEVEL SECURITY;

-- spare_requisitions
GRANT ALL ON TABLE public.spare_requisitions TO service_role;
REVOKE ALL ON TABLE public.spare_requisitions FROM anon, authenticated, PUBLIC;
ALTER TABLE public.spare_requisitions ENABLE ROW LEVEL SECURITY;

-- stock_issues
GRANT ALL ON TABLE public.stock_issues TO service_role;
REVOKE ALL ON TABLE public.stock_issues FROM anon, authenticated, PUBLIC;
ALTER TABLE public.stock_issues ENABLE ROW LEVEL SECURITY;

-- notices
GRANT ALL ON TABLE public.notices TO service_role;
REVOKE ALL ON TABLE public.notices FROM anon, authenticated, PUBLIC;
ALTER TABLE public.notices ENABLE ROW LEVEL SECURITY;

-- document_categories
GRANT ALL ON TABLE public.document_categories TO service_role;
REVOKE ALL ON TABLE public.document_categories FROM anon, authenticated, PUBLIC;
ALTER TABLE public.document_categories ENABLE ROW LEVEL SECURITY;

-- visualizations
GRANT ALL ON TABLE public.visualizations TO service_role;
REVOKE ALL ON TABLE public.visualizations FROM anon, authenticated, PUBLIC;
ALTER TABLE public.visualizations ENABLE ROW LEVEL SECURITY;

-- timesheets_backup_20261014_holiday
GRANT ALL ON TABLE public.timesheets_backup_20261014_holiday TO service_role;
REVOKE ALL ON TABLE public.timesheets_backup_20261014_holiday FROM anon, authenticated, PUBLIC;
ALTER TABLE public.timesheets_backup_20261014_holiday ENABLE ROW LEVEL SECURITY;

-- ── Section B: other repo-created tables found without RLS ───
-- Found 2026-10-18 while writing the guardrail test: these three
-- are created by repo migrations with no ENABLE ROW LEVEL SECURITY
-- anywhere, and backend routers read/write them (artisan_timesheets:
-- app/routers/artisan_timesheets.py; lookup_lists:
-- app/routers/lookup_lists.py + breakdowns.py auto-learn upsert;
-- ppe_matrix: app/routers/ppe.py, soft dependency). Guarded by
-- to_regclass so the block is a no-op for any table not (yet)
-- live — unlike Section A, live existence is inferred, not audited.
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['artisan_timesheets', 'lookup_lists', 'ppe_matrix'] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', t);
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated, PUBLIC', t);
      EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
      RAISE NOTICE 'RLS enabled on public.%', t;
    ELSE
      RAISE NOTICE 'public.% does not exist — skipped', t;
    END IF;
  END LOOP;
END $$;

-- ── Verification (run these; each must return ZERO rows) ────
-- 1. Listed tables that exist live but still lack RLS:
SELECT c.relname AS table_missing_rls
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
  AND c.relname IN (
    'employees', 'leaves', 'overtime', 'duty_roster', 'duty_rotations',
    'rotation_covers', 'standby_rotations', 'standby_schedules', 'drivers',
    'password_reset_requests', 'work_orders', 'breakdowns', 'daily_reports',
    'sheq_reports', 'sheq_findings', 'sheq_inspections', 'ppe_records',
    'spare_parts', 'spare_requisitions', 'stock_issues', 'notices',
    'document_categories', 'visualizations',
    'timesheets_backup_20261014_holiday', 'artisan_timesheets',
    'lookup_lists', 'ppe_matrix'
  );

-- 2. Residual anon/authenticated/PUBLIC grants on those tables:
SELECT table_name, grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public'
  AND table_name IN (
    'employees', 'leaves', 'overtime', 'duty_roster', 'duty_rotations',
    'rotation_covers', 'standby_rotations', 'standby_schedules', 'drivers',
    'password_reset_requests', 'work_orders', 'breakdowns', 'daily_reports',
    'sheq_reports', 'sheq_findings', 'sheq_inspections', 'ppe_records',
    'spare_parts', 'spare_requisitions', 'stock_issues', 'notices',
    'document_categories', 'visualizations',
    'timesheets_backup_20261014_holiday', 'artisan_timesheets',
    'lookup_lists', 'ppe_matrix'
  )
  AND grantee IN ('anon', 'authenticated', 'PUBLIC');

-- ── Rollback (COMMENTED — only if this migration must be undone)
-- Re-disabling RLS re-exposes these tables to the anon key; there is
-- no automated rollback of the REVOKEs because the exact prior grant
-- topology is unknown. If the old open state is truly needed, run the
-- DISABLE lines below AND re-grant explicitly, e.g.
--   GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.<t> TO anon, authenticated;
-- ALTER TABLE public.employees DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.leaves DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.overtime DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.duty_roster DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.duty_rotations DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.rotation_covers DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.standby_rotations DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.standby_schedules DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.drivers DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.password_reset_requests DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.work_orders DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.breakdowns DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.daily_reports DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.sheq_reports DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.sheq_findings DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.sheq_inspections DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.ppe_records DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.spare_parts DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.spare_requisitions DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.stock_issues DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.notices DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.document_categories DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.visualizations DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE public.timesheets_backup_20261014_holiday DISABLE ROW LEVEL SECURITY;
