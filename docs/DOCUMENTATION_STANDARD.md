# Backend documentation standard

**Audience:** developers, IDE language services, API consumers, and coding agents

## Documentation layers

| Layer | Format | Purpose |
|---|---|---|
| Repository instructions | `AGENTS.md` | First-read constraints and documentation links |
| Architecture and domain rules | Markdown in `docs/` | Decisions, workflows, invariants, and verification |
| Python contracts | PEP 257 Google-style docstrings | IDE help and Sphinx autodoc |
| HTTP contracts | FastAPI models and route metadata | OpenAPI, Swagger UI, and ReDoc |
| Generated reference | Sphinx + autodoc + Napoleon + MyST | Searchable Python and Markdown documentation |

Markdown remains the narrative source format. Sphinx consumes it through MyST,
so the project does not need parallel `.rst` copies.

## What must be documented

Document exported modules, classes, functions, dependencies, and route helpers
when callers need to understand:

- business invariants or record ownership;
- authorization and role requirements;
- database reads, writes, transactions, caching, or other side effects;
- exceptions and HTTP failure semantics;
- units, date boundaries, pagination, defaults, or ordering;
- fallback behavior and whether a failure may safely degrade.

Do not narrate obvious Python syntax. Keep exact request and response shapes in
Pydantic models and OpenAPI rather than copying them into prose.

## Docstring style

Use a one-line imperative or descriptive summary followed, when useful, by
Google-style sections:

```python
def load_records(limit: int) -> list[dict]:
    """Load records in stable newest-first order.

    Args:
        limit: Maximum rows to return.

    Returns:
        JSON-ready records.

    Raises:
        HTTPException: If the backing service is unavailable.
    """
```

Never include credentials, service-role keys, or real business records in
examples.

## Maintenance rule

A change to authorization, persistence, failure semantics, pagination, or a
public function signature must update its source docstring and the nearest
architecture or domain document in the same change.

