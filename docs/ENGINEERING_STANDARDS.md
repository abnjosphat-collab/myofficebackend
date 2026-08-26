# Backend engineering standards

Short, on purpose. Four rules, each pointing at a real example already in this
codebase — this is the wiring standard, not a process. Not enforced by CI yet;
follow it because it's the pattern that's already proven out, not because
something will block your PR if you don't.

## 1. New CRUD-shaped endpoints use `CrudRouter`

If an endpoint is list/get/create/update/delete against one table, don't
hand-write it — instantiate `CrudRouter` (`app/crud_router.py`) and add any
genuinely custom endpoints alongside it. It already handles search, equality
filters, ordering, pagination, `exclude_unset` on PATCH (so an explicit null
still clears a field — this exact bug had to be fixed by hand more than once
before the router existed), and the `require_role('manager')` delete gate.

Model to copy: `app/routers/contractors.py` — `CrudRouter` fronts the plain
CRUD, then a couple of hand-added routes (`get_contractor` with a joined
`contractor_jobs` sub-resource) sit right below it for the part that isn't
generic. `app/routers/accounting.py` shows the same pattern at larger scale —
CrudRouter for the base, `db_helpers.get_or_404` / `apply_date_range` /
`distinct_suggestions` reused directly for the rest.

Skip it only when there's a specific, named reason — e.g. the endpoint isn't
really CRUD-shaped (an analysis/aggregation endpoint, a multi-table merge like
timesheets' `effectiveTimesheets`). That's a real reason. "It's already
written the old way" isn't.

## 2. Never fake a 200 on failure

If something goes wrong, let the exception propagate (there's a global
handler in `main.py` that logs full detail server-side and returns a clean
generic 500) or raise `HTTPException` with a real status code. Don't do
either of these:

```python
except Exception as e:
    logger.error(...)
    return {"metrics": {...all zeros...}, "success": True}   # looks healthy, isn't
```

```python
except Exception as e:
    return []   # looks like "no data", not "the DB call failed"
```

Both shapes exist in this codebase today (`breakdowns.py`'s dashboard
endpoint, `compressors.py` in a few places) and at least one has a confirmed
live consequence: a DB hiccup makes the homepage KPI show "0 Open
Breakdowns," indistinguishable from an actually-healthy plant. Being on the
remediation list doesn't mean it's fine to add a new one meanwhile.

Also avoid `raise HTTPException(500, detail=f"Error: {str(e)}")` where it can
be avoided — that leaks the raw internal error string to the client. Let it
propagate to the global handler instead, which logs the detail server-side
and returns a generic client-facing message. (`main.py` already has a
comment flagging this as known tech debt in ~280 existing call sites — not
something to fix everywhere at once, but don't add more of it.)

## 3. Tests: two sanctioned recipes, pick whichever fits

No real DB in tests, ever — Supabase is always mocked. Two patterns coexist
here on purpose, for different situations:

- **Mount a real (minimal) FastAPI app + `TestClient`**, when the thing under
  test is the HTTP/routing layer itself — request shape, status codes, query
  param handling. Model: `tests/test_crud_router.py`. For this style, import
  the shared fake from `tests/conftest.py` (`FakeSupabase`, `FakeQuery`,
  `FakeResult`) rather than hand-rolling another one — it's a generic,
  chain-recording Supabase double that fits any CrudRouter-backed endpoint.
- **Call the route handler coroutine directly**, when what's under test is
  the router's own internal computation, not FastAPI plumbing. Model:
  `tests/test_ppe_matrix.py` — no app, no TestClient, just
  `await set_ppe_matrix_entry(MatrixEntry(...), current_user=...)`. Routers
  with real multi-table business logic (matrix recalculation, null-clearing
  update payloads) usually need a bespoke fake local to that test file rather
  than the shared one in `conftest.py` — that's fine, don't force-fit it.

`asyncio_mode = auto` is set in `pytest.ini`, so `async def test_*` needs no
decorator.

## 4. Coverage is visible now — use it, don't chase a number

`pytest -q --cov=app --cov-report=term-missing` (also what CI runs) shows
exactly which lines in which router aren't hit by anything. It's report-only,
not a gate — there's no threshold to hit. When you're touching a router
anyway, glance at its coverage line and add a test for the path you just
changed if there's a cheap one; don't go chasing unrelated files' numbers.
