# Remediation report — backend (Phases 1–2), 2026-10-18 scan

Independent quality scan dated 2026-10-18, remediated per the owner-supplied
5-phase prompt. This file covers the backend repo (Phases 1–2). The frontend
repo (Phases 3–5) is reported in `frontend/docs/REMEDIATION_REPORT_2026-10-18.md`.

Standing rules observed throughout: no commit/push/deploy, no migrations run,
no live Supabase writes (SQL files only), no new packages, business
rules/permissions/API unchanged except the fixes listed below.

## Phase 1 — RLS SQL (files written, NOT applied)

New migration files (repo root, untracked until the owner stages them):

| File | Lines | Purpose |
| ---- | ----: | ------- |
| `supabase_migration_enable_rls_public_tables.sql` | 233 | Enable RLS on public tables with per-table policies |
| `supabase_migration_lock_down_functions.sql` | 164 | Lock down SECURITY DEFINER / function exposure |
| `supabase_migration_function_search_path.sql` | 79 | Pin `search_path` on functions |
| `supabase_migration_housekeeping_indexes.sql` | 64 | Index hygiene — drops commented out, NOT for blind apply |

Supporting work:

- `tests/test_migrations_rls.py` (new) — asserts the migration files exist,
  parse, and cover the expected tables/policies.
- `docs/RLS_AND_API_EXPOSURE.md` (new) — the exposure model: what RLS now
  guards, what stays open and why.
- `docs/ENGINEERING_STANDARDS.md` — open-reads table added; fixed a stale
  `accounting.py` reference to `tasks_events`.
- 5 read endpoints stay deliberately open (pinned in code as `OPEN_READS`,
  tabled in ENGINEERING_STANDARDS). This is a decision, not a gap.

Before/after: before, public-table RLS was unasserted and several functions
ran with ambient privileges; after, the SQL to close that is written,
reviewed in-file, and covered by a migration test — but it is **not live**
until Owner gate 1 (run the files in Supabase in order).

Skips (owner decisions, gate 3): `housekeeping_indexes.sql` drops stay
commented (4 redundant + 2 duplicate-definition indexes found; drops need an
owner word); `timesheets_backup_20261014_holiday` and `app/timesheets - Copy/`
left untouched.

## Phase 2 — backend auth/honesty

Honest errors, no leak, no fake success:

- `app/errors.py` (new) — single `server_error` helper; every handler returns
  a sanitised 500 with a digest, never internals.
- `app/clock.py` (new) — injectable clock for time-dependent logic/tests.
- `str(e)` in responses: 215 → 0 (codemod `scripts/codemod_server_error.py`,
  kept in repo).
- `.dict()` → `.model_dump()`: done across routers (remaining 5 hits are
  comments only; codemod `scripts/codemod_model_dump.py` kept).
- `tests/test_no_internal_error_leak.py` (new) — fails the suite if any
  error path leaks tracebacks/exception text.
- `tests/test_router_smoke_untested.py` (new, 16 tests) — smoke-covers
  routers with no dedicated tests via `dependency_overrides`.

Update-payload bugs fixed (exclude_unset):

- `app/routers/employees.py` bulk-normalize + `tests/test_employees_bulk_normalize.py`.
- `app/routers/equipment.py` PATCH + `tests/test_equipment_crud.py`.
- `training` cleared after review (its forms cannot express null; no bug).

Auth hardening:

- 2FA fail-closed for enrolled users; `REQUIRE_MFA_AAL2` defaults true as an
  emergency off-switch. One enrolled-user login test still needs the owner
  (gate 2).
- In-process cache only, TTL 30s, never tokens in Redis; role lag documented
  in `docs/CACHING.md` (new).
- `app/rate_limit.py` + `tests/test_rate_limit.py`; password-reset-requests
  migration file written (`supabase_migration_password_reset_requests.sql`,
  19 lines, unapplied).
- `tests/test_endpoint_auth.py`, `tests/test_security_verification.py`
  updated for the new error contract.

Hygiene (backend side): `.gitignore` now covers `node_modules/`, `*.db`,
`uploads/`, root `package.json`/`package-lock.json`; deleted the stray
`node_modules/pandas` shim tree, root `package.json`/`package-lock.json`,
`myoffice.db`, `spares.db`. `uploads/notices/` kept (attachments live in
Supabase `ams-documents`, not the repo).

## Verification (backend)

| Check | Result |
| ----- | ------ |
| `pytest -q` | **1916 passed** (was 1849/1898 at start) |
| `scripts/check_pyright_baseline.py` | **196/196** (was 198) |
| `str(e)` leak grep | 0 hits in responses |
| Full diff | 86 tracked files, +1882/−510; warnings-free `git diff --stat` |

## Owner gates (backend parts)

1. **Run the Phase 1 SQL in Supabase, in file order**, and confirm the
   `SUPABASE_KEY` on Render is the service_role key. Exposure stays live
   until this runs.
2. Set `TRUST_PROXY_HEADERS=true` only if the proxy is trusted; run one
   enrolled-MFA-user login test; enable Supabase leaked-password protection.
3. Decide: `timesheets_backup_20261014_holiday`, `timesheets - Copy/`
   (7-file active experiment diff), the 5 deliberate open reads, Tools
   self-registration, and whether to apply `housekeeping_indexes.sql`.

## Not verified

- The Phase 1 SQL has never been executed — no live Supabase was touched in
  this run (per instructions). Syntax/shape is asserted by
  `test_migrations_rls.py`, not by a live apply.
- The production login/MFA path against Render + Supabase was not exercised.
