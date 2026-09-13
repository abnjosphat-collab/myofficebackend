# Testing (backend repo)

MyOffice has **two independent test suites** (backend + frontend). Both are fast, need no live server, database, or Redis, and mock external services.

## This repo (pytest)

```bash
# one-time: install test deps into the existing venv
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt
# run
./venv/Scripts/python.exe -m pytest -q
```

Covers (`tests/`):

- **test_auth.py** — `role_at_least`, `get_current_user`, `require_role` (RBAC). Supabase mocked.
- **test_cache.py** — Redis response-cache layer with in-memory fake.
- **test_uploads.py** — upload validation (extension allowlist + size cap).
- **test_endpoint_auth.py** — write/delete endpoints return 401 without token.
- **test_timesheets_pagination.py** — timesheet list fetches all pages (NEC periods).

These verify logic and auth gates — not full DB behaviour. Test deps live in `requirements-dev.txt`, not production `requirements.txt`.

## Frontend repo (Vitest)

```bash
cd ../frontend   # sibling checkout
npm test
npx tsc --noEmit
```

See frontend `docs/TESTING.md` for details.
