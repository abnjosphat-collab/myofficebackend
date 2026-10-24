# tests/test_issues_scope.py — stock-issues scoping incl. the Q6 name fallback.
#
# recipient_id is authoritative when present; a missing id falls back to
# matching recipient_name against in-scope people. Anything else fails closed.
# The scoped list predicates (in-scope id OR NULL id) and name-filters the
# NULLs in Python; the unscoped list is the generic CrudRouter path verbatim.

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _split_top(expr):
    """Split an or_() expression on top-level commas (respecting parens/quotes)."""
    parts, depth, cur, instr = [], 0, "", False
    for ch in expr:
        if ch == '"':
            instr = not instr
            cur += ch
        elif not instr and ch == "(":
            depth += 1
            cur += ch
        elif not instr and ch == ")":
            depth -= 1
            cur += ch
        elif not instr and depth == 0 and ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _apply_or(rows, expr):
    """Honour PostgREST or_() filters: ilike search, in.(...) lists, is.null."""
    kept = []
    for part in _split_top(expr):
        if ".ilike." in part:
            col, pat = part.split(".ilike.", 1)
            # PostgREST value-quoting ("...") is stripped server-side before
            # the LIKE match; unquoted patterns pass through unchanged.
            term = pat.strip().strip('"').strip("%").lower()
            kept += [r for r in rows if term in str(r.get(col) or "").lower()]
        elif ".in.(" in part:
            col, rest = part.split(".in.(", 1)
            vals = json.loads("[" + rest.rstrip(")") + "]")
            kept += [r for r in rows if r.get(col) in vals]
        elif part.endswith(".is.null"):
            col = part[: -len(".is.null")]
            kept += [r for r in rows if r.get(col) is None]
        else:  # pragma: no cover - unknown filter shape fails the test loudly
            raise AssertionError(f"unhandled or_ part: {part!r}")
    seen, out = set(), []
    for r in kept:
        if id(r) not in seen:
            seen.add(id(r))
            out.append(r)
    return out


class _Query:
    def __init__(self, table, store, calls):
        self._table = table
        self._store = store
        self._calls = calls
        self._rows = list(store[table])
        self._start = 0
        self._end = None
        self._pending_update = None
        self._deleting = False

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def in_(self, col, vals):
        self._calls.append(("in", col, list(vals)))
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def or_(self, s):
        self._calls.append(("or_", s))
        self._rows = _apply_or(self._rows, s)
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, r.get(col) or ""), reverse=bool(desc))
        return self

    def limit(self, n):
        self._calls.append(("limit", n))
        self._end = self._start + n
        return self

    def offset(self, n):
        self._calls.append(("offset", n))
        self._start = n
        return self

    def range(self, start, end):
        self._start, self._end = start, end + 1
        return self

    def insert(self, payload):
        new_id = max([r.get("id", 0) for r in self._store[self._table]] or [0]) + 1
        row = {"id": new_id, **payload}
        self._store[self._table].append(row)
        self._rows = [row]
        return self

    def update(self, payload):
        self._pending_update = payload
        return self

    def delete(self):
        self._deleting = True
        return self

    def execute(self):
        if self._pending_update is not None:
            for r in self._rows:
                r.update(self._pending_update)
        if self._deleting:
            doomed = {id(r) for r in self._rows}
            self._store[self._table] = [r for r in self._store[self._table] if id(r) not in doomed]
            self._rows = []
        return SimpleNamespace(data=[dict(r) for r in self._rows[self._start:self._end]])


class _FakeSupabase:
    def __init__(self, store, calls):
        self._store = store
        self._calls = calls

    def table(self, name):
        return _Query(name, self._store, self._calls)


def _issue(i, recipient_id, recipient_name):
    return {"id": i, "recipient_id": recipient_id, "recipient_name": recipient_name,
            "issued_at": "2020-01-01T00:00:00+00:00",
            "items": [{"stock_code": "S1", "description": "Bolt", "qty": 2}]}


def _store():
    return {
        "employees": [
            {"id": 1, "employee_id": "C1001", "section": "Projects",
             "first_name": "Pam", "last_name": "Moyo"},
            {"id": 2, "employee_id": "C2001", "section": "Mechanical",
             "first_name": "Mike", "last_name": "Doe"},
        ],
        "stock_issues": [
            _issue(1, "C1001", "Pam Moyo"),
            _issue(2, "C2001", "Mike Doe"),
            _issue(3, None, "Pam Moyo"),      # NULL id, in-scope name
            _issue(4, None, "Stranger X"),    # NULL id, out-of-scope name
        ],
    }


SCOPED = {"user_id": "u-1", "role": "manager", "scope": "Projects"}
UNSCOPED = {"user_id": "u-9", "role": "manager", "scope": None}


@pytest.fixture
def issues_client(monkeypatch):
    import app.routers.issues as mod
    from app.auth import get_current_user as real_gcu

    store, calls, stock_calls = _store(), [], []
    current = [UNSCOPED]
    monkeypatch.setattr(mod, "supabase", _FakeSupabase(store, calls))
    monkeypatch.setattr(mod, "adjust_stock",
                        lambda *a, **k: (stock_calls.append((a, k)), {"stock": [], "warnings": []})[1])

    async def _override():
        return current[0]

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/issues")
    # Routes captured the real auth callables at import; override them here.
    # (DELETE depends on a require_role("manager") closure, not get_current_user.)
    app.dependency_overrides[real_gcu] = _override
    delete_dep = next(d.dependency for d in mod._delete_issue.__defaults__ if hasattr(d, "dependency"))
    app.dependency_overrides[delete_dep] = _override
    client = TestClient(app, raise_server_exceptions=False)
    client.as_user = lambda user: current.__setitem__(0, user)  # noqa: E731
    client.issue_store = store
    client.issue_calls = calls
    client.stock_calls = stock_calls
    return client


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()]


def test_scoped_list_sees_id_matches_and_name_fallback_only(issues_client):
    issues_client.as_user(SCOPED)
    assert _ids(issues_client.get("/api/issues")) == [1, 3]


def test_unscoped_list_is_the_generic_path_verbatim(issues_client):
    issues_client.as_user(UNSCOPED)
    assert _ids(issues_client.get("/api/issues")) == [1, 2, 3, 4]
    assert ("limit", 500) in issues_client.issue_calls
    assert ("offset", 0) in issues_client.issue_calls
    assert [c for c in issues_client.issue_calls if c[0] == "or_"] == []


def test_scoped_list_honours_limit_on_the_slice(issues_client):
    issues_client.as_user(SCOPED)
    assert _ids(issues_client.get("/api/issues?limit=1")) == [1]


def test_scoped_create_for_out_of_scope_id_is_404_and_moves_no_stock(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.post("/api/issues", json={
        "recipient_id": "C2001", "recipient_name": "Mike Doe",
        "items": [{"description": "Bolt", "qty": 1}]})
    assert resp.status_code == 404
    assert len(issues_client.issue_store["stock_issues"]) == 4
    assert issues_client.stock_calls == []


def test_scoped_create_with_null_id_and_in_scope_name_passes(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.post("/api/issues", json={
        "recipient_name": "pam  moyo",
        "items": [{"description": "Bolt", "qty": 1}]})
    assert resp.status_code == 200, resp.text
    assert len(issues_client.issue_store["stock_issues"]) == 5
    assert issues_client.stock_calls != []


def test_scoped_create_with_null_id_and_stranger_name_is_404(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.post("/api/issues", json={
        "recipient_name": "Stranger X", "items": [{"description": "Bolt", "qty": 1}]})
    assert resp.status_code == 404
    assert len(issues_client.issue_store["stock_issues"]) == 4


def test_scoped_patch_of_out_of_scope_row_is_404(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.patch("/api/issues/2", json={"notes": "hi"})
    assert resp.status_code == 404
    assert "notes" not in issues_client.issue_store["stock_issues"][1]


def test_scoped_patch_moving_recipient_out_of_scope_writes_nothing(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.patch("/api/issues/1", json={"recipient_id": "C2001"})
    assert resp.status_code == 404
    assert issues_client.issue_store["stock_issues"][0]["recipient_id"] == "C1001"


def test_scoped_patch_within_scope_passes(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.patch("/api/issues/1", json={"notes": "hi"})
    assert resp.status_code == 200, resp.text
    assert issues_client.issue_store["stock_issues"][0]["notes"] == "hi"


def test_scoped_delete_of_out_of_scope_row_is_404_and_keeps_it(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.delete("/api/issues/2")
    assert resp.status_code == 404
    assert len(issues_client.issue_store["stock_issues"]) == 4
    assert issues_client.stock_calls == []


def test_scoped_stats_count_only_visible_rows(issues_client):
    issues_client.as_user(SCOPED)
    resp = issues_client.get("/api/issues/stats/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["unique_recipients"] == 1


def test_unscoped_stats_count_everything(issues_client):
    issues_client.as_user(UNSCOPED)
    resp = issues_client.get("/api/issues/stats/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert body["unique_recipients"] == 3
