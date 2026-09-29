# Backend documentation map

Start here when taking over backend work.

| Need | Source of truth |
|---|---|
| Runtime architecture and setup | Repository `README.md` one directory above `docs/` |
| Routing, error, testing, and typing rules | [`ENGINEERING_STANDARDS.md`](./ENGINEERING_STANDARDS.md) |
| Documentation conventions | [`DOCUMENTATION_STANDARD.md`](./DOCUMENTATION_STANDARD.md) |
| Tools backend contract | [`TOOLS_WORKSPACE.md`](./TOOLS_WORKSPACE.md) |
| Test commands and scope | [`TESTING.md`](./TESTING.md) |
| Runtime HTTP contract | FastAPI `/openapi.json`, `/docs`, and `/redoc` |

Install `requirements-docs.txt`, then build the inspectable reference with:

```bash
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. Generated HTML is local output and is not
committed.
