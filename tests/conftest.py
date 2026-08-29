# tests/conftest.py — shared pytest fixtures/fakes.
#
# One canonical fake Supabase client for tests against a *generic* CrudRouter-backed
# endpoint: chain-recording (.select/.eq/.or_/.order/.limit/.offset/.insert/.update/
# .delete), single flat `store["data"]` payload for execute(). This is the right shape
# whenever what's under test is "does the query get built correctly / does data pass
# through unchanged" — which is what every CrudRouter router needs, and was previously
# hand-rolled fresh in test_crud_router.py alone.
#
# Business-logic-heavy routers that touch MULTIPLE tables with different per-table
# behaviour (e.g. ppe.py's matrix recalculation in test_ppe_matrix.py, or
# requisitions.py's update-payload shape in test_requisitions_null_clearing.py) are
# legitimately better served by a bespoke, purpose-built fake local to that test file.
# Don't force those onto this one — they're verifying data transformations across
# tables, not query-building mechanics, and a one-size-fits-all fake would either lose
# fidelity for them or bloat this one with generality only they'd use.


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    """Records every call and is fully chainable; execute() returns the canned rows."""
    def __init__(self, table, store):
        self.table_name = table
        self.store = store
        store["queries"].append(self)
        self.calls = []

    def select(self, *a, **k):     self.calls.append(("select", a));       return self
    def eq(self, col, val):        self.calls.append(("eq", col, val));    return self
    def or_(self, s):              self.calls.append(("or_", s));          return self
    def order(self, col, desc=False): self.calls.append(("order", col, desc)); return self
    def limit(self, n):            self.calls.append(("limit", n));        return self
    def offset(self, n):           self.calls.append(("offset", n));       return self
    def range(self, start, end):   self.calls.append(("range", start, end)); return self
    def insert(self, d):           self.calls.append(("insert", d)); self.store["insert"] = d; return self
    def update(self, d):           self.calls.append(("update", d)); self.store["update"] = d; return self
    def delete(self):              self.calls.append(("delete",));         return self

    def execute(self):
        return FakeResult(self.store["data"])


class FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return FakeQuery(name, self.store)
