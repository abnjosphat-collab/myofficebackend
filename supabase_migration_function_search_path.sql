-- ============================================================
-- MyOffice security hardening 3/3 — pin search_path on public
-- functions WITHOUT one.
-- File: backend/supabase_migration_function_search_path.sql
-- Date: 2026-10-18.
--
-- *** OPTIONAL — UNTESTED AGAINST LIVE DATA ***
-- Run files 1/3 (enable RLS) and 2/3 (lock down functions) first;
-- this file is lower priority and has NOT been validated against
-- the live schema or live data. Review the NOTICE output and the
-- verification query before considering it done, and skip it
-- entirely if any locked-down function misbehaves afterwards.
--
-- Why: a function without an explicit search_path resolves
-- unqualified names against the CALLER's search_path, which lets a
-- hostile caller shadow tables (search_path hijack). Pinning
-- "public, pg_temp" (pg_temp LAST, so temp objects cannot shadow
-- real ones either) closes that for all plain functions at once.
--
-- Skips, by design:
--   * Extension-owned functions (pg_depend deptype 'e') — owned by
--     their extension; altering them fights future extension
--     upgrades and Supabase-managed objects.
--   * Functions returning trigger — behaviour-sensitive: trigger
--     helpers (handle_new_user, set_updated_at, bump_row_version,
--     maintenance_events_append_only, ...) run in firing-statement
--     contexts where a pinned path could subtly change resolution.
--   * Functions that already set search_path (e.g. the three
--     tools_workspace_* RPCs, which carry set search_path=public).
--   * Procedures and aggregates (prokind <> 'f').
-- Idempotent: re-running finds nothing left to change (the WHERE
-- clause only matches functions still missing the setting).
-- ============================================================

DO $$
DECLARE
  r RECORD;
  n INT := 0;
BEGIN
  FOR r IN
    SELECT p.oid, n.nspname, p.proname,
           pg_get_function_identity_arguments(p.oid) AS args
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND p.prokind = 'f'                          -- plain functions only
      AND p.prorettype <> 'trigger'::regtype       -- skip trigger helpers
      AND NOT EXISTS (                             -- skip extension-owned
        SELECT 1 FROM pg_depend d
        JOIN pg_extension e ON e.oid = d.refobjid
        WHERE d.objid = p.oid AND d.deptype = 'e'
      )
      AND coalesce(array_to_string(p.proconfig, ','), '') NOT LIKE '%search_path=%'
  LOOP
    EXECUTE format('ALTER FUNCTION %I.%I(%s) SET search_path = public, pg_temp',
                   r.nspname, r.proname, r.args);
    RAISE NOTICE 'pinned search_path on %.%(%)', r.nspname, r.proname, r.args;
    n := n + 1;
  END LOOP;
  RAISE NOTICE 'done: pinned search_path on % function(s)', n;
END $$;

-- ── Verification (must return ZERO rows) ─────────────────────
-- Public plain non-trigger non-extension functions still missing
-- an explicit search_path setting:
SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.prokind = 'f'
  AND p.prorettype <> 'trigger'::regtype
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    JOIN pg_extension e ON e.oid = d.refobjid
    WHERE d.objid = p.oid AND d.deptype = 'e'
  )
  AND coalesce(array_to_string(p.proconfig, ','), '') NOT LIKE '%search_path=%';

-- ── Rollback (COMMENTED — per function, no bulk undo exists) ──
-- There is no record of which functions this file changed; reset
-- individually if one misbehaves, e.g.:
-- ALTER FUNCTION public.<name>(<identity args>) RESET search_path;
