# MyOffice backend agent guide

- Read [`README.md`](./README.md) for runtime architecture and commands.
- Read [`docs/README.md`](./docs/README.md) before changing public APIs or domain behavior.
- Follow [`docs/ENGINEERING_STANDARDS.md`](./docs/ENGINEERING_STANDARDS.md) for routing, error, test, and typing rules.
- Public Python contracts use PEP 257 Google-style docstrings. Validate generated documentation with `python -m sphinx -W --keep-going -b html docs docs/_build/html` after installing `requirements-docs.txt`.
- Preserve the real HTTP status on failure; never turn an unavailable dependency into an empty successful result.
- Do not run migrations, change live Supabase data, deploy, commit, or push unless the user explicitly asks.
- The whole project's status, decisions and open items are in the frontend repository's `docs/CURRENT_HANDOFF.md` (`myofficefrontend`); this repo is the FastAPI backend it talks to.
- Work on a branch, not `main`: a push to `main` deploys the backend to production (Render). Never blanket-commit the working tree; stage by path.
- Done means verified: `pytest -q --cov=app --cov-fail-under=90` and `python scripts/check_pyright_baseline.py` (the type-error count must not rise; it is currently above its limit on `main`, so do not add to it). Run the tests with the project's virtual environment, not a global Python.
- Schema changes are `supabase_migration_*.sql` files run by a person in the Supabase SQL editor; write them, never apply them without the owner's say-so.
