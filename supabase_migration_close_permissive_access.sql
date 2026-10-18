-- ============================================================
-- MyOffice security hardening 4/4 - close permissive access
-- File: backend/supabase_migration_close_permissive_access.sql
-- Date: 2026-10-18. Owner runs this in the Supabase SQL Editor AFTER
--   supabase_migration_enable_rls_public_tables.sql and
--   supabase_migration_lock_down_functions.sql (both already applied).
-- Rollback: backend/supabase_rollback_close_permissive_access.sql
-- Idempotent: every statement is safe to re-run.
--
-- Problem (measured live 2026-10-18, after files 1-3 were applied):
--   * 21 tables had RLS ON but a policy granting role `public` (which
--     includes the anon key) USING (true) for ALL commands: equipment,
--     spares, timesheets, requisitions, requisition_items, compressors,
--     compressor_readings, daily_entries, alerts, service_records,
--     service_intervals, maintenance_schedule, safety_complaints,
--     corrective_actions, nearmiss_reports, pachedu_reports,
--     pto_reports, pto_action_plan, vfl_reports, vfl_action_plan,
--     work_stoppage_reports. Anyone with the public anon key could read,
--     change or delete them over the REST API, with no sign-in.
--   * About 25 more tables allowed ANY signed-in user full access
--     ("Authenticated users full access", auth_all_*): accounting_*,
--     documents, services, sop_*, contractors, job_cards and others. That
--     lets a viewer-level account bypass the backend's role checks.
--   * `anon` and `authenticated` still held ALL privileges on all 83
--     public tables and views.
--
-- Evidence this cannot break the app (recorded 2026-10-18):
--   * The frontend touches ONE table directly: user_profiles
--     (frontend/lib/supabase.ts: select, select, insert). It uses no
--     other .from(), no .rpc(), no realtime channel, no storage call.
--   * The backend reaches every other table with the service_role key
--     (app/supabase_client.py), which bypasses RLS. Its only anon-key
--     use is a password check against Auth (routers/signatures.py).
--   * user_signatures is used only by the backend (service role).
--
-- Fix:
--   A. Every public table except user_profiles: RLS on, ALL privileges
--      revoked from anon / authenticated / PUBLIC, ALL granted to
--      service_role, and every policy dropped (they are all
--      allow-everyone or allow-signed-in rules that nothing relies on).
--   B. user_profiles keeps what the sign-in flow needs and no more:
--      authenticated may SELECT and INSERT own row (id, email,
--      full_name, avatar_url only, so a new profile can never choose
--      its own role or permissions). UPDATE/DELETE are revoked: the
--      frontend never updates profiles, and admin edits go through the
--      backend. anon gets nothing.
--   C. The 5 views and performance_cache: revoke anon/authenticated.
--   D. Default privileges: new tables and functions in public no longer
--      auto-grant to anon/authenticated, so this cannot silently recur.
--
-- Not touched on purpose: storage buckets and their policies (public
-- download URLs are used by the document hub), Auth settings, sequences,
-- data, and the supabase_admin default-privilege entries (see note D).
-- ============================================================

-- -- A. Every public table except user_profiles ---------------
DO $$
DECLARE r record; p record;
BEGIN
  FOR r IN
    SELECT c.relname
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> 'user_profiles'
    ORDER BY c.relname
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.relname);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated, PUBLIC', r.relname);
    EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', r.relname);
    FOR p IN SELECT policyname FROM pg_policies WHERE schemaname = 'public' AND tablename = r.relname LOOP
      EXECUTE format('DROP POLICY %I ON public.%I', p.policyname, r.relname);
    END LOOP;
  END LOOP;
END $$;

-- -- B. user_profiles: the one table the browser reads directly --
REVOKE ALL ON TABLE public.user_profiles FROM anon, PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.user_profiles FROM authenticated;
GRANT SELECT ON TABLE public.user_profiles TO authenticated;
GRANT INSERT (id, email, full_name, avatar_url) ON TABLE public.user_profiles TO authenticated;
GRANT ALL ON TABLE public.user_profiles TO service_role;

-- -- C. Views and the materialised view ------------------------
DO $$
DECLARE v text;
BEGIN
  FOREACH v IN ARRAY ARRAY[
    'compressor_statistics', 'monthly_performance', 'compressor_daily_summary',
    'compressor_service_overview', 'employees_with_string_id', 'performance_cache'
  ] LOOP
    IF to_regclass('public.' || v) IS NOT NULL THEN
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon, authenticated, PUBLIC', v);
      EXECUTE format('GRANT ALL ON TABLE public.%I TO service_role', v);
    END IF;
  END LOOP;
END $$;

-- -- D. Stop new objects being open by default -----------------
-- Applies to objects created by the role running this file (postgres in the
-- SQL Editor). Objects created by supabase_admin keep Supabase's defaults.
-- A future table the browser must read directly needs an explicit GRANT plus
-- RLS policies; a future function the backend calls via .rpc() is already
-- executable by service_role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

-- ============================================================
-- Verification (run these; 1, 2 and 3 must return ZERO rows)
-- ============================================================
-- 1. Any anon/authenticated/PUBLIC privilege left on a table other than user_profiles:
SELECT table_name, grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated', 'PUBLIC')
  AND table_name <> 'user_profiles';

-- 2. Any public table without RLS:
SELECT c.relname AS table_without_rls
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relrowsecurity;

-- 3. Any policy left outside user_profiles:
SELECT tablename, policyname FROM pg_policies
WHERE schemaname = 'public' AND tablename <> 'user_profiles';

-- 4. user_profiles should show ONLY authenticated SELECT (no anon, no UPDATE/DELETE):
SELECT grantee, privilege_type FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND table_name = 'user_profiles'
  AND grantee IN ('anon', 'authenticated', 'PUBLIC');
--    ...and column-level INSERT only on id, email, full_name, avatar_url:
SELECT grantee, column_name, privilege_type FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name = 'user_profiles'
  AND grantee = 'authenticated' AND privilege_type = 'INSERT' ORDER BY column_name;

-- After running: sign in to MyOffice, open a list page (Personnel), and open Tools.
-- If anything fails, run backend/supabase_rollback_close_permissive_access.sql.
