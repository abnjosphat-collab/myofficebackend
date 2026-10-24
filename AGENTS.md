# MyOffice backend agent guide

- Read [`README.md`](./README.md) for runtime architecture and commands.
- Read [`docs/README.md`](./docs/README.md) before changing public APIs or domain behavior.
- Follow [`docs/ENGINEERING_STANDARDS.md`](./docs/ENGINEERING_STANDARDS.md) for routing, error, test, and typing rules.
- Public Python contracts use PEP 257 Google-style docstrings. Validate generated documentation with `python -m sphinx -W --keep-going -b html docs docs/_build/html` after installing `requirements-docs.txt`.
- **Data integrity:** follow [`docs/DATA_INTEGRITY_STANDARD.md`](./docs/DATA_INTEGRITY_STANDARD.md) for any write endpoint: rules are enforced on the server, who/when come from the session, writes to payroll and people tables are audited, errors go through `server_error`. `tests/test_data_integrity_rules.py` fails when these are broken.
- Preserve the real HTTP status on failure; never turn an unavailable dependency into an empty successful result.
- Do not run migrations, change live Supabase data, deploy, commit, or push unless the user explicitly asks.
- The whole project's status, decisions and open items are in the frontend repository's `docs/CURRENT_HANDOFF.md` (`myofficefrontend`); this repo is the FastAPI backend it talks to.
- The engineering process (five steps, Definition of Done, release checklist) is the frontend repository's `docs/ENGINEERING_PROCESS.md` (`myofficefrontend`); follow it for every batch.
- Work on a branch, not `main`: a push to `main` deploys the backend to production (Render). Never blanket-commit the working tree; stage by path.
- Done means verified: `pytest -q --cov=app --cov-fail-under=90` and `python scripts/check_pyright_baseline.py` (the type-error count must not rise; it is currently above its limit on `main`, so do not add to it). Run the tests with the project's virtual environment, not a global Python.
- Schema changes and data fixes are `supabase_*.sql` files. CHANGED 20 Oct 2026 (owner): the assistant may now run such a file against the live project itself (Supabase MCP `execute_sql`) when the owner has asked for that file to be run. Before running: read-only dry run of exactly what it will touch; a backup of any rows it changes or deletes, taken inside the file; one transaction with a guard that stops it if the data is not what was checked; then verify afterwards with read-only queries and say what changed. Still never run anything the owner has not asked for, and never one that drops tables or columns, truncates, or deletes without a backup.
