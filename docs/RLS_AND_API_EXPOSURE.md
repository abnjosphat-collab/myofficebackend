# RLS and API exposure

One-page reference for the Phase 1 database hardening (2026-10-18).
Authoritative details live in the migration file headers; this page
is the why and the run order.

## Why RLS-on-with-no-policies is correct here

Every MyOffice table is backend-only. The FastAPI backend connects
with the **service-role key**, and `service_role` **bypasses RLS**,
so it reads and writes regardless of policies. The browser uses the
**anon key** and touches exactly one table directly — `user_profiles`
(3 call sites in `frontend/lib/supabase.ts`); it has zero `.rpc()`
calls and the backend's only anon-key use is a password check
(`signatures.py`, GoTrue API, no table access).

So: RLS enabled + no policies = backend unaffected, anon blocked on
everything. No per-table policies are needed or wanted; adding any
would only widen access.

## Owner run order (project `geomlsxphwaxpaloweky`)

Run in the Supabase SQL Editor, or via
`scripts/apply_migration.py <file>`, in this order:

1. `supabase_migration_enable_rls_public_tables.sql` — grants
   service_role, revokes anon/authenticated/PUBLIC, enables RLS on
   the 24 exposed tables (+3 repo-created extras), drops the two
   over-broad SHEQ policies.
2. `supabase_migration_lock_down_functions.sql` — revokes EXECUTE
   on the 7 public functions (re-granting service_role, and
   authenticated for `my_role()` which RLS policies evaluate),
   sets `security_invoker=true` on the 5 views, locks down
   `performance_cache`.
3. `supabase_migration_function_search_path.sql` — **optional,
   untested against live data**. Pins `search_path` on public
   functions missing it. Skip unless comfortable.

## Verification

Each file ends with live `SELECT`s that **must return zero rows**
(tables without RLS, residual anon grants, unwanted EXECUTE grants,
views without `security_invoker`). Rollback lines are in the same
files, commented out.

## Rule for future tables

Every new migration that creates a table must `ENABLE ROW LEVEL
SECURITY` in that same file (create tables via SQL, not the Table
Editor auto-grants — or revoke afterwards). The guardrail test
`backend/tests/test_migrations_rls.py` fails CI-style on any
`CREATE TABLE` without RLS coverage, so a gap cannot slip in
silently again.
