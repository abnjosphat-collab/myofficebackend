# Backend engineering standards

**See also:** [PRODUCT.md](./PRODUCT.md) and workspace `docs/WORK_ORDERS.md` for work-order rules.

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
generic. `app/routers/tasks_events.py` shows the same pattern at larger scale —
CrudRouter for the base, hand-added progress-comment sub-resource routes below
it, and the router mounted behind a manager-only gate in `main.py`.

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

Both shapes existed in this codebase (`breakdowns.py`'s dashboard endpoint,
`compressors.py`'s trend/comparison/management-summary endpoints — all now
fixed) and at least one had a confirmed live consequence: a DB hiccup made
the homepage KPI show "0 Open Breakdowns," indistinguishable from an
actually-healthy plant. A prior instance being fixed doesn't mean it's fine
to add a new one — this has been found and fixed independently in at least
6 different files across this project's history; treat every new
`except Exception` block as a place this bug likes to hide.

Never `raise HTTPException(500, detail=f"Error: {str(e)}")` — that leaks the
raw internal error string to the client. Route 500s through
`app/errors.py`'s `server_error(exc, what)` instead: it logs the traceback
server-side with a short ref id and returns a generic
`"{what} failed. Please try again. (ref …)"` message. (The ~280 call sites
`main.py`'s comment used to flag as tech debt were converted mechanically in
2026-10; `tests/test_no_internal_error_leak.py` parses every router and fails
on any 500 detail that references the caught exception.)

## 3. Tests: two sanctioned recipes, pick whichever fits

Default tests mock Supabase. Two patterns coexist here on purpose, for
different situations. A **narrow, opt-in disposable Postgres suite** is
allowed when a feature's invariants cannot be proven by mocks (unique
custody, rollback, concurrent issue). That suite must skip unless
`PORTABLE_TOOLS_TEST_DATABASE_URL` (or a future similarly named DSN) is set
to a disposable database — never production — and a green run without that
DSN must not be described as proof of transaction behaviour.

Two mocked patterns:

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

## 4. Coverage and typing are regression-gated

CI runs `pytest -q --cov=app --cov-report=term-missing --cov-fail-under=90`.
The floor protects the established whole-application baseline; it is not a
reason to add low-value tests or avoid testing a changed failure path.

Pyright still has inherited debt, so `scripts/check_pyright_baseline.py` is a
blocking ratchet rather than a false clean claim. It fails when the error count
exceeds the recorded maximum and asks maintainers to lower that maximum when a
change improves it. New type errors are therefore blocked while the existing
debt is paid down incrementally.

## Appendix: open reads — what and why

Most reads require a signed-in user, and every write does. The reads below
are deliberately open (no `get_current_user` gate) and pinned in
`tests/test_endpoint_auth.py`'s `OPEN_READS` — a 401 on any of them is a bug,
and so is any other data-bearing read NOT requiring auth. Owner decision,
recorded 2026-10.

| Open read | Why it stays open |
|---|---|
| `GET /api/availabilities` | Equipment + latest availability merged for the homepage; no PII, no per-user data |
| `GET /api/availabilities/stats` | Aggregate counts only |
| `GET /api/breakdowns/dashboard/overview` | Homepage KPI aggregates (counts/sums, cached 60 s); individual breakdowns stay gated |
| `GET /api/equipment` | The equipment list isn't PII; the single-item read stays gated |
| `GET /api/leaves/stats/summary` | Aggregate counts; individual leave records stay gated |
| `GET /api/lookup-lists/{list_name}` | Shared pick-list values (locations, natures) needed by unsigned forms |
| `GET /api/maintenance/work-orders/stats/summary` | Aggregate counts; work orders themselves stay gated |
| `GET /api/timesheets/stats/summary` | Aggregate counts; individual timesheets stay gated |
| `GET /api/health`, `/api/version`, `*/health*` | Liveness probes; status only, never exception strings or DB details |

Related owner decisions from the same pass: Tools self-registration and
first-account-is-admin are unchanged (rate-limited instead), and the
per-username Tools failed-login counter is in-process (resets on restart,
counted per worker).
