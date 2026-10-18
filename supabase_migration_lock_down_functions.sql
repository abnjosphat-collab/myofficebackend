-- ============================================================
-- MyOffice security hardening 2/3 — lock down functions, views,
-- and the performance cache
-- File: backend/supabase_migration_lock_down_functions.sql
-- Date: 2026-10-18. Owner runs this in the Supabase SQL Editor
-- (or scripts/apply_migration.py) AFTER
-- supabase_migration_enable_rls_public_tables.sql.
-- Idempotent: every statement is safe to re-run.
--
-- Problem: several SECURITY DEFINER functions are EXECUTEable by
-- anyone (default PUBLIC grant), and five views run with the
-- view owner's rights instead of the caller's (security_invoker
-- off), so anon-key PostgREST callers inherit owner power.
--
-- Fix: REVOKE EXECUTE FROM PUBLIC, anon, authenticated on each
-- function the backend calls, then GRANT it back explicitly TO
-- service_role. The GRANT is load-bearing, not belt-and-braces:
-- revoking from PUBLIC also strips service_role (it held EXECUTE
-- via PUBLIC), so without the explicit grant the backend's own
-- .rpc() calls would break. service_role bypasses RLS but still
-- needs the EXECUTE privilege itself.
--
-- Pre-change grep evidence (2026-10-18, recorded here per spec):
-- Backend .rpc() call sites — ALL via the shared service-role
-- singleton (app/supabase_client.py, SUPABASE_KEY), none via the
-- anon key, none from the frontend (frontend has zero .rpc()):
--   * get_user_role_by_id   — backend/app/auth.py:77
--   * sync_issue_item_prices — backend/app/routers/spares.py:708
--   * tools_workspace_apply_change      — tools_workspace.py:121
--   * tools_workspace_next_register_number — tools_workspace.py:198
--   * tools_workspace_restore_change    — tools_workspace.py:768
-- NOTE: stale comments (auth.py:5,70; migration_auth.sql:108) say
-- the role lookup exists "so the anon client can call it". That is
-- outdated: no anon client calls it; only the service-role backend
-- does. Hence authenticated is revoked below too.
-- my_role() is NOT called via .rpc() anywhere; it is evaluated
-- inside RLS policies (user_profiles "Admins update any profile"
-- in supabase_migration_auth.sql:50-54, and maintenance_schedules
-- "Managers write schedules" in
-- supabase_migration_maintenance_schedules.sql:81-84), and a policy
-- expression needs EXECUTE for the querying role — so authenticated
-- KEEPS my_role() (PUBLIC and anon are revoked).
-- handle_new_user() is trigger-only (on_auth_user_created on
-- auth.users); it fires as its SECURITY DEFINER owner, so no
-- GRANT is needed for it at all.
-- Views (compressor_statistics, monthly_performance,
-- compressor_daily_summary, compressor_service_overview,
-- employees_with_string_id) and performance_cache are live-first
-- objects: zero references anywhere in backend/ or frontend/
-- source, so no code path depends on their current exposure.
-- End-of-file verification SELECTs must each return zero rows.
-- ============================================================

-- ── 1. Tools-workspace atomic command RPCs ───────────────────
-- Backend: tools_workspace.py:121 (issue/transfer/return/... all
-- funnel through apply_change). Already granted to service_role in
-- supabase_migration_tools_workspace.sql:60 — re-granted here so
-- this file is self-sufficient regardless of run history.
REVOKE EXECUTE ON FUNCTION public.tools_workspace_apply_change(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tools_workspace_apply_change(jsonb) TO service_role;

-- Backend: tools_workspace.py:768 (undo/redo).
REVOKE EXECUTE ON FUNCTION public.tools_workspace_restore_change(uuid, text, boolean) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tools_workspace_restore_change(uuid, text, boolean) TO service_role;

-- Backend: tools_workspace.py:198 (register numbers).
REVOKE EXECUTE ON FUNCTION public.tools_workspace_next_register_number(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.tools_workspace_next_register_number(text) TO service_role;

-- ── 2. Spares price-propagation RPC ──────────────────────────
-- Backend: spares.py:708, wrapped in try/except because the function
-- "may not exist yet (migration pending)" — live existence is
-- UNCERTAIN, hence the guarded block. Signature per the CREATE
-- FUNCTION comment at spares.py:681 (TEXT, FLOAT8). If the live
-- signature differs, \df the function and adjust the line below.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public' AND p.proname = 'sync_issue_item_prices') THEN
    REVOKE EXECUTE ON FUNCTION public.sync_issue_item_prices(text, double precision) FROM PUBLIC, anon, authenticated;
    GRANT EXECUTE ON FUNCTION public.sync_issue_item_prices(text, double precision) TO service_role;
    RAISE NOTICE 'locked down sync_issue_item_prices(text, double precision)';
  ELSE
    RAISE NOTICE 'sync_issue_item_prices() not present — nothing to lock down (backend treats it as optional)';
  END IF;
END $$;

-- ── 3. Auth trigger + role lookup ────────────────────────────
-- Trigger-only; fires as SECURITY DEFINER owner. No GRANT needed.
REVOKE EXECUTE ON FUNCTION public.handle_new_user() FROM PUBLIC, anon, authenticated;

-- Backend: auth.py:77, on EVERY authenticated request (role fetch).
-- service_role grant is what keeps sign-in working after the revoke.
REVOKE EXECUTE ON FUNCTION public.get_user_role_by_id(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_user_role_by_id(uuid) TO service_role;

-- ── 4. my_role() — authenticated MUST keep EXECUTE ───────────
-- Needed by RLS policies that evaluate it as the querying user
-- (user_profiles admin policy; maintenance_schedules manager
-- policy). Revoking authenticated here would break admin profile
-- edits and manager schedule writes for signed-in users.
REVOKE EXECUTE ON FUNCTION public.my_role() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.my_role() TO authenticated, service_role;

-- ── 5. Views → run with caller rights ────────────────────────
-- security_invoker=true makes each view apply the CALLER's RLS
-- policies instead of the view owner's. Backend (service_role,
-- RLS bypass) is unaffected. IF EXISTS: these are live-first
-- objects; a missing view is a NOTICE, not a failed migration.
ALTER VIEW IF EXISTS public.compressor_statistics SET (security_invoker = true);
ALTER VIEW IF EXISTS public.monthly_performance SET (security_invoker = true);
ALTER VIEW IF EXISTS public.compressor_daily_summary SET (security_invoker = true);
ALTER VIEW IF EXISTS public.compressor_service_overview SET (security_invoker = true);
ALTER VIEW IF EXISTS public.employees_with_string_id SET (security_invoker = true);

-- ── 6. performance_cache ─────────────────────────────────────
-- Live-first object, unreferenced in all repo code. SELECT is
-- revoked from anon/authenticated/PUBLIC; service_role gets an
-- explicit ALL grant first so any service-key reader/writer keeps
-- working. Guarded: a missing object is a NOTICE, not an error.
DO $$
BEGIN
  IF to_regclass('public.performance_cache') IS NOT NULL THEN
    GRANT ALL ON TABLE public.performance_cache TO service_role;
    REVOKE SELECT ON TABLE public.performance_cache FROM anon, authenticated, PUBLIC;
    RAISE NOTICE 'locked down public.performance_cache';
  ELSE
    RAISE NOTICE 'public.performance_cache does not exist — skipped';
  END IF;
END $$;

-- ── Verification (run these; each must return ZERO rows) ────
-- 1. Unwanted EXECUTE grants left on the seven functions
--    (my_role/authenticated is the one intentional exception):
SELECT routine_name, grantee, privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public'
  AND routine_name IN (
    'tools_workspace_apply_change', 'tools_workspace_restore_change',
    'tools_workspace_next_register_number', 'sync_issue_item_prices',
    'handle_new_user', 'get_user_role_by_id', 'my_role'
  )
  AND grantee IN ('PUBLIC', 'anon', 'authenticated')
  AND NOT (routine_name = 'my_role' AND grantee = 'authenticated');

-- 2. Views still running with owner rights:
SELECT c.relname AS view_without_security_invoker
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'v'
  AND c.relname IN (
    'compressor_statistics', 'monthly_performance',
    'compressor_daily_summary', 'compressor_service_overview',
    'employees_with_string_id'
  )
  AND NOT (coalesce(c.reloptions, ARRAY[]::text[]) @> ARRAY['security_invoker=true']);

-- 3. Residual anon/authenticated SELECT on performance_cache:
SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND table_name = 'performance_cache'
  AND privilege_type = 'SELECT' AND grantee IN ('anon', 'authenticated', 'PUBLIC');

-- ── Rollback (COMMENTED — only if this migration must be undone)
-- GRANT EXECUTE ON FUNCTION public.tools_workspace_apply_change(jsonb) TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.tools_workspace_restore_change(uuid, text, boolean) TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.tools_workspace_next_register_number(text) TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.sync_issue_item_prices(text, double precision) TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.handle_new_user() TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.get_user_role_by_id(uuid) TO PUBLIC;
-- GRANT EXECUTE ON FUNCTION public.my_role() TO PUBLIC;
-- ALTER VIEW public.compressor_statistics SET (security_invoker = false);
-- ALTER VIEW public.monthly_performance SET (security_invoker = false);
-- ALTER VIEW public.compressor_daily_summary SET (security_invoker = false);
-- ALTER VIEW public.compressor_service_overview SET (security_invoker = false);
-- ALTER VIEW public.employees_with_string_id SET (security_invoker = false);
-- GRANT SELECT ON TABLE public.performance_cache TO anon, authenticated;
