# MyOffice backend agent guide

- Read [`README.md`](./README.md) for runtime architecture and commands.
- Read [`docs/README.md`](./docs/README.md) before changing public APIs or domain behavior.
- Follow [`docs/ENGINEERING_STANDARDS.md`](./docs/ENGINEERING_STANDARDS.md) for routing, error, test, and typing rules.
- Public Python contracts use PEP 257 Google-style docstrings. Validate generated documentation with `python -m sphinx -W --keep-going -b html docs docs/_build/html` after installing `requirements-docs.txt`.
- Preserve the real HTTP status on failure; never turn an unavailable dependency into an empty successful result.
- Do not run migrations, change live Supabase data, deploy, commit, or push unless the user explicitly asks.

