# tests/scope_harness.py — shared rig for the Batch 2 scope-behaviour tests.
#
# Mounts a REAL router on a fresh app with a fake Supabase that honours
# eq/neq/in_/or_ filters and performs real insert/upsert/update/delete, so
# "refused" also proves "nothing written". Auth is overridden wholesale
# (role gating is covered elsewhere; here only the section limit varies).

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import get_current_user as _real_gcu
from tests.test_issues_scope import _apply_or


SCOPED = {"user_id": "u-1", "email": "scoped@example.com", "role": "user", "scope": "Projects"}
SCOPED_MGR = {"user_id": "u-2", "email": "sm@example.com", "role": "manager", "scope": "Projects"}
UNSCOPED = {"user_id": "u-9", "email": "admin@example.com", "role": "manager", "scope": None}


def employees_store():
    return [
        {"id": 1, "employee_id": "C1001", "section": "Projects",
         "first_name": "Pam", "last_name": "Moyo", "department": "Engineering",
         "position": "Fitter", "designation": "Fitter", "employment_type": "NEC",
         "archived": False, "date_of_engagement": "2020-01-01", "id_number": "ID1"},
        {"id": 2, "employee_id": "C2001", "section": "Mechanical",
         "first_name": "Mike", "last_name": "Doe", "department": "Engineering",
         "position": "Fitter", "designation": "Fitter", "employment_type": "NEC",
         "archived": False, "date_of_engagement": "2020-01-01", "id_number": "ID2"},
    ]


class _Query:
    def __init__(self, table, store, calls):
        self._table = table
        self._store = store
        self._calls = calls
        self._rows = list(store.get(table, []))
        self._start = 0
        self._end = None
        self._pending_update = None
        self._deleting = False
        self._single = False
        self._count = None

    def select(self, *a, **k):
        self._count = k.get("count")
        return self

    def eq(self, col, val):
        self._calls.append(("eq", col, val))
        # PostgREST coerces a string id against an int column ("42" matches
        # 42); the fake must too, or str-typed link fields 404 against int
        # parents they match in production.
        def _same(row):
            current = row.get(col)
            if current == val:
                return True
            return (current is not None and val is not None
                    and str(current) == str(val))
        self._rows = [r for r in self._rows if _same(r)]
        return self

    def neq(self, col, val):
        self._calls.append(("neq", col, val))
        self._rows = [r for r in self._rows if r.get(col) != val]
        return self

    def _cmp(self, name, col, val, op):
        self._calls.append((name, col, val))

        def _hit(row):
            current = row.get(col)
            if current is None or val is None:
                return False
            try:
                return op(current, val)
            except TypeError:
                # Mixed date/str or int/str: compare the rendered forms, as
                # PostgREST would after casting the unknown literal.
                return op(str(current), str(val))

        self._rows = [r for r in self._rows if _hit(r)]
        return self

    def gte(self, col, val):
        import operator
        return self._cmp("gte", col, val, operator.ge)

    def lte(self, col, val):
        import operator
        return self._cmp("lte", col, val, operator.le)

    def gt(self, col, val):
        import operator
        return self._cmp("gt", col, val, operator.gt)

    def lt(self, col, val):
        import operator
        return self._cmp("lt", col, val, operator.lt)

    def in_(self, col, vals):
        self._calls.append(("in", col, list(vals)))
        # Same PostgREST int/str coercion as eq(): in.(1) matches TEXT '1'.
        wanted = list(vals)
        wanted_str = {str(v) for v in wanted if v is not None}

        def _hit(row):
            current = row.get(col)
            if current in wanted:
                return True
            return current is not None and str(current) in wanted_str

        self._rows = [r for r in self._rows if _hit(r)]
        return self

    @property
    def not_(self):
        outer = self

        class _Not:
            def is_(self, col, val):
                outer._rows = [r for r in outer._rows if r.get(col) is not None]
                return outer

        return _Not()

    def single(self):
        self._single = True
        return self

    def is_(self, col, val):
        if isinstance(val, str) and val.lower() == "null":
            self._rows = [r for r in self._rows if r.get(col) is None]
        else:
            self._rows = [r for r in self._rows if r.get(col) == val]
        return self

    def maybe_single(self):
        self._single = True
        return self

    def or_(self, s):
        self._calls.append(("or_", s))
        self._rows = _apply_or(self._rows, s)
        return self

    def ilike(self, col, pattern):
        self._calls.append(("ilike", col, pattern))
        term = str(pattern).strip("%").lower()
        self._rows = [r for r in self._rows if term in str(r.get(col) or "").lower()]
        return self

    def contains(self, col, vals):
        self._calls.append(("contains", col, list(vals)))
        wanted = [str(v).lower() for v in vals]
        self._rows = [r for r in self._rows
                      if all(w in [str(t).lower() for t in (r.get(col) or [])] for w in wanted)]
        return self

    def order(self, col, desc=False):
        self._rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col) or "")),
                        reverse=bool(desc))
        return self

    def limit(self, n):
        self._end = self._start + n
        return self

    def offset(self, n):
        self._start = n
        return self

    def range(self, start, end):
        self._start, self._end = start, end + 1
        return self

    def insert(self, payload):
        rows = payload if isinstance(payload, list) else [payload]
        table_rows = self._store.setdefault(self._table, [])
        nums = [r.get("id") for r in table_rows if isinstance(r.get("id"), int)]
        new_id = max(nums or [0])
        inserted = []
        for p in rows:
            new_id += 1
            row = {"id": new_id, **p}
            table_rows.append(row)
            inserted.append(row)
        self._rows = inserted
        return self

    def upsert(self, payload, on_conflict=None):
        rows = payload if isinstance(payload, list) else [payload]
        table_rows = self._store.setdefault(self._table, [])
        have = {r.get(on_conflict) for r in table_rows} if on_conflict else set()
        inserted = []
        for p in rows:
            if on_conflict and p.get(on_conflict) in have:
                continue
            if on_conflict:
                have.add(p.get(on_conflict))
            table_rows.append(dict(p))
            inserted.append(dict(p))
        self._rows = inserted
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
        # Real responses always carry .count: the pre-page total when the
        # query asked for an exact count, else None.
        total = len(self._rows) if self._count else None
        if self._deleting:
            doomed = {id(r) for r in self._rows}
            # Real Supabase returns the deleted rows; routers 404 on an empty
            # set, so the fake must too.
            out = [dict(r) for r in self._rows[self._start:self._end]]
            self._store[self._table] = [r for r in self._store[self._table] if id(r) not in doomed]
            self._rows = []
        else:
            out = [dict(r) for r in self._rows[self._start:self._end]]
        if self._single:
            out = out[0] if out else None
        return SimpleNamespace(data=out, count=total)


class FakeSupabase:
    def __init__(self, store, calls):
        self._store = store
        self._calls = calls

    def table(self, name):
        return _Query(name, self._store, self._calls)


def mount_router(monkeypatch, module, prefix, store):
    """Mount module.router with faked Supabase; client.as_user swaps identity."""
    calls = []
    current = [UNSCOPED]
    fake = FakeSupabase(store, calls)
    if hasattr(module, "supabase"):
        monkeypatch.setattr(module, "supabase", fake)
    # The generic CrudRouter layer reads its own binding: modules that mix
    # generic routes with hand-rolled ones (handover, job cards) need both
    # patched, and patching it is inert for modules that never use it.
    import app.crud_router as cr
    monkeypatch.setattr(cr, "supabase", fake)

    # Direct require_role("x")(auth_header) calls (bulk gates) run past
    # dependency_overrides: fake the gate with a real role check.
    def _fake_require_role(min_role):
        async def _gate(authorization=None):
            from app.auth import ROLE_ORDER
            from fastapi import HTTPException
            have = current[0].get("role", "viewer")
            if ROLE_ORDER.index(have) < ROLE_ORDER.index(min_role):
                raise HTTPException(status_code=403, detail="Forbidden")
            return current[0]

        return _gate

    if hasattr(module, "require_role"):
        monkeypatch.setattr(module, "require_role", _fake_require_role)

    # require_role_if_status_in is row-independent (it gates on the body's
    # status value), so it legitimately runs before visibility checks; fake it
    # with the same role semantics.
    if hasattr(module, "require_role_if_status_in"):
        async def _fake_status_gate(status, allowed, min_role, authorization=None, context=None):
            from app.auth import ROLE_ORDER
            from fastapi import HTTPException
            if status in allowed:
                have = current[0].get("role", "viewer")
                if ROLE_ORDER.index(have) < ROLE_ORDER.index(min_role):
                    raise HTTPException(status_code=403, detail="Forbidden")
            return current[0]

        monkeypatch.setattr(module, "require_role_if_status_in", _fake_status_gate)

    async def _override():
        return current[0]

    app = FastAPI()
    own_prefix = getattr(module.router, "prefix", None)
    app.include_router(module.router, prefix="" if own_prefix else prefix)
    app.dependency_overrides[_real_gcu] = _override

    seen = {_real_gcu}

    def _walk(dependant):
        for sub in dependant.dependencies:
            fn = sub.call
            if fn is not None and fn not in seen:
                seen.add(fn)
                app.dependency_overrides[fn] = _override
            _walk(sub)

    for route in module.router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is not None:
            _walk(dependant)

    client = TestClient(app, raise_server_exceptions=False)
    client.as_user = lambda user: current.__setitem__(0, user)  # noqa: E731
    client.scope_store = store
    client.scope_calls = calls
    return client
