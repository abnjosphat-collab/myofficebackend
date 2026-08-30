# tests/_compressors_fake.py — shared in-memory fake Supabase client for
# app/routers/compressors.py's test files.
#
# Not a test module itself (leading underscore keeps it out of pytest's
# `test_*.py` collection — see pytest.ini) — an importable helper shared by
# tests/test_compressors_*.py.
#
# compressors.py is unlike most business-logic routers this session: it's one
# file that touches SIX different tables (compressors, compressor_readings,
# service_records, maintenance_schedule, alerts, service_intervals) with real
# cross-row logic that depends on genuine eq/gt/lt/order/limit filtering and
# genuine insert/update/delete mutation (e.g. create_daily_entry_cumulative
# finds "the previous reading before this date" by filtering+sorting a list
# purely in Python, then mutates the readings table; check_and_update_
# service_due decides update-existing vs insert-new based on a real eq
# lookup). The conftest.py FakeSupabase (chain-recording, returns one static
# canned payload regardless of filters) can't exercise any of that — and per
# conftest.py's own docstring, a business-logic-heavy multi-table router is
# exactly the documented case for a bespoke fake instead of forcing the
# generic one. This one is shared across compressors' several test files
# (rather than copy-pasted into each, which is the more common per-file
# pattern elsewhere) because the six-table, real-filtering behavior is
# identical for every one of them — duplicating it six times would just be
# six copies to keep in sync.

from typing import Any, Dict, List, Optional, Tuple


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name: str, state: "_FakeState"):
        self.table_name = table_name
        self.state = state
        self._eq: Dict[str, Any] = {}
        self._gt: Optional[Tuple[str, Any]] = None
        self._lt: Optional[Tuple[str, Any]] = None
        self._gte: Optional[Tuple[str, Any]] = None
        self._lte: Optional[Tuple[str, Any]] = None
        self._order_col: Optional[str] = None
        self._order_desc: bool = False
        self._limit: Optional[int] = None
        self._op: Optional[Tuple[str, Any]] = None

    # --- query builder (chainable, mirrors the subset of the Supabase client
    # compressors.py actually calls) ---
    def select(self, *a, **k): return self
    def eq(self, col, val): self._eq[col] = val; return self
    def gt(self, col, val): self._gt = (col, val); return self
    def lt(self, col, val): self._lt = (col, val); return self
    def gte(self, col, val): self._gte = (col, val); return self
    def lte(self, col, val): self._lte = (col, val); return self

    def order(self, col, desc=False, asc=None):
        # compressors.py calls both `.order("date", asc=True)` and
        # `.order("date", desc=True)` / `.order(..., desc=False)` — normalize
        # to a single desc flag.
        self._order_col = col
        self._order_desc = (not asc) if asc is not None else desc
        return self

    def limit(self, n): self._limit = n; return self
    def insert(self, data): self._op = ("insert", data); return self
    def update(self, data): self._op = ("update", data); return self
    def delete(self): self._op = ("delete", None); return self

    def _matching_rows(self) -> List[dict]:
        rows = self.state.tables.get(self.table_name, [])
        for col, val in self._eq.items():
            rows = [r for r in rows if r.get(col) == val]
        if self._gt:
            c, v = self._gt
            rows = [r for r in rows if r.get(c) is not None and r.get(c) > v]
        if self._lt:
            c, v = self._lt
            rows = [r for r in rows if r.get(c) is not None and r.get(c) < v]
        if self._gte:
            c, v = self._gte
            rows = [r for r in rows if r.get(c) is not None and r.get(c) >= v]
        if self._lte:
            c, v = self._lte
            rows = [r for r in rows if r.get(c) is not None and r.get(c) <= v]
        if self._order_col:
            rows = sorted(rows, key=lambda r: r.get(self._order_col) or "", reverse=self._order_desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def execute(self):
        op_name = self._op[0] if self._op else "select"
        key = (self.table_name, op_name)
        if key in self.state.fail_once:
            msg = self.state.fail_once.pop(key)
            raise Exception(msg)
        if self.table_name in self.state.always_fail:
            raise Exception(self.state.always_fail[self.table_name])

        rows = self._matching_rows()

        if self._op is None:
            return _Resp(rows)

        op, data = self._op
        if op == "insert":
            new_rows = data if isinstance(data, list) else [data]
            self.state.tables.setdefault(self.table_name, []).extend(new_rows)
            return _Resp(new_rows)
        if op == "update":
            for r in rows:
                r.update(data)
            return _Resp(rows)
        if op == "delete":
            table = self.state.tables.setdefault(self.table_name, [])
            for r in rows:
                table.remove(r)
            return _Resp(rows)
        raise AssertionError(f"unreachable op {op!r}")


class _FakeState:
    def __init__(self, tables: Optional[Dict[str, list]] = None):
        # deep-ish copy so mutation in one test doesn't leak into another's fixture literal
        self.tables: Dict[str, list] = {k: [dict(row) for row in v] for k, v in (tables or {}).items()}
        self.fail_once: Dict[Tuple[str, str], str] = {}
        self.always_fail: Dict[str, str] = {}


class FakeSupabase:
    """In-memory Supabase stand-in with real eq/gt/lt/gte/lte/order/limit
    filtering and real insert/update/delete mutation, keyed by table name.

    tables: {table_name: [row_dict, ...]} initial fixture data.
    """

    def __init__(self, tables: Optional[Dict[str, list]] = None):
        self.state = _FakeState(tables)

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(name, self.state)

    def fail_once(self, table: str, op: str, message: str = "simulated failure"):
        """Make the NEXT execute() of (table, op) raise `message`, then behave
        normally again — for testing a single-retry recovery path."""
        self.state.fail_once[(table, op)] = message

    def always_fail(self, table: str, message: str = "simulated failure"):
        """Make every execute() against `table` raise `message` — for testing
        a route's generic DB-failure handling."""
        self.state.always_fail[table] = message
