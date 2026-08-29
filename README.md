# MyOffice — Backend

FastAPI backend for the MyOffice ERP: personnel, maintenance/work orders, safety
(SHEQ), timesheets/leaves/overtime, inventory/spares, and a handful of demo
verticals (bank, room rental, etc.). Supabase (Postgres) is the database; there's
no separate ORM layer — routers talk to Supabase directly via `app/supabase_client.py`.

See the sibling [`frontend/`](../frontend) repo for the Next.js UI that consumes
this API, and the root [`README.md`](../README.md) for how the two run together.

## Quick start

```bash
python -m venv venv
./venv/Scripts/activate          # Windows; `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt

cp .env.example .env             # then fill in SUPABASE_URL / SUPABASE_KEY at minimum
uvicorn main:app --reload        # or: startbackend.bat on Windows
```

The API is now at `http://localhost:8000`. Interactive docs (Swagger) are at
`/docs`, ReDoc at `/redoc` — both work out of the box since neither is disabled.

### Environment variables

See `.env.example` for the full list with explanations. The only two required to
boot are `SUPABASE_URL` and `SUPABASE_KEY` (a service-role key — this backend is
the trusted server side, RLS policies assume requests come through it). Redis
(`REDIS_URL`), rate limiting, and Sentry are all optional — the app degrades
gracefully without them (see `app/cache.py`'s in-memory fallback when Redis is
unreachable).

## Architecture

- **`main.py`** — FastAPI app setup: CORS (origins from `ALLOWED_ORIGINS`), global
  exception handler, router registration (two mechanisms exist historically — a
  loop for most routers, explicit `include_router` calls for the exceptions — see
  code comments), startup logging.
- **`app/routers/`** — one file per resource/module (`employees.py`, `maintenance.py`,
  `timesheets.py`, etc.), ~45 files. Most follow the same shape: Pydantic request/
  response models, `GET`/`POST`/`PUT`/`DELETE` handlers, `Depends(get_current_user)`
  or `Depends(require_role(...))` on anything that isn't a public read.
- **`app/crud_router.py`** — a `CrudRouter` base class that a handful of genuinely
  boilerplate routers (competency, failure_modes, condition_monitoring, production,
  contractors, handover, job_cards) inherit from, collapsing their 4 CRUD handlers
  to config. Most routers do **not** use this — they have real domain logic
  (computed fields, joins, non-standard validation) that would make forcing them
  onto a shared base counterproductive; that was a deliberate, case-by-case
  decision, not an oversight.
- **`app/auth.py`** — Supabase JWT verification, role hierarchy
  (`viewer < user < manager < admin < super_admin`), `require_role()` dependency
  factory.
- **`app/cache.py`** — Redis-backed response caching (`@cached` decorator) with a
  namespace-based invalidation scheme, falls back to no-op if Redis is unreachable.
- **`app/serialization.py`** — shared helpers for shaping Supabase records into
  JSON (e.g. `convert_dates_to_iso`).
- **`supabase_migration_*.sql`** at the repo root — one file per schema change,
  meant to be run manually in the Supabase SQL editor. There's still no
  runner (the `supabase` client is REST-based, no direct Postgres connection
  is configured — it can't execute arbitrary DDL, and production schema
  changes should be reviewed by hand anyway), but there is now real
  applied/pending **tracking**: after running a file, record it with
  `python scripts/track_migration.py --mark-applied <filename>`; check status
  with `--list`. Requires `supabase_migration_schema_migrations_table.sql` to
  have been run first (bootstraps the tracking table itself). Check `git log`
  on a given file if you need to know when/why it was *added* — the tracker
  is for when/whether it was *run*.

## Testing

See [`../TESTING.md`](../TESTING.md) for the full picture (both repos). Short
version:

```bash
./venv/Scripts/python.exe -m pip install -r requirements-dev.txt   # one-time
./venv/Scripts/python.exe -m pytest
```

Tests mock Supabase/Redis — no live database, no network, no server needed.
