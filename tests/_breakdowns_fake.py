# tests/_breakdowns_fake.py — shared in-memory fake Supabase client for
# app/routers/breakdowns.py's test files.
#
# Not a test module itself (leading underscore keeps it out of pytest's
# `test_*.py` collection — see pytest.ini) — an importable helper shared by
# tests/test_breakdowns_*.py.
#
# breakdowns.py's CRUD endpoints touch TWO tables with real filtering
# behavior: "breakdowns" itself (eq/order/range for get_breakdowns,
# eq-by-id for get/update/delete) and "lookup_lists" (learn_lookup_value's
# best-effort eq+ilike existence check, then a conditional insert) — a
# second table the parent create/update call must never let a failure in
# propagate back as a failed save. Modeled on tests/_compressors_fake.py's
# real-filtering approach (not conftest.py's generic single-table
# chain-recorder) because what's under test here is genuine filter/pagination
# correctness and genuine two-table side effects, not just "does the query
# get built."

from typing import Any, Dict, List, Optional, Tuple


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table_name: str, state: "_FakeState"):
        self.table_name = table_name
        self.state = state
        self._eq: Dict[str, Any] = {}
        self._ilike: Optional[Tuple[str, str]] = None
        self._order_col: Optional[str] = None
        self._order_desc: bool = False
        self._range: Optional[Tuple[int, int]] = None
        self._op: Optional[Tuple[str, Any]] = None

    def select(self, *a, **k): return self
    def eq(self, col, val): self._eq[col] = val; return self
    def ilike(self, col, val): self._ilike = (col, val); return self
    def order(self, col, desc=False): self._order_col = col; self._order_desc = desc; return self
    def range(self, start, end): self._range = (start, end); return self
    def limit(self, n): return self  # health_check only; no filtering needed here
    def insert(self, data): self._op = ("insert", data); return self
    def update(self, data): self._op = ("update", data); return self
    def delete(self): self._op = ("delete", None); return self

    def _matching_rows(self) -> List[dict]:
        rows = self.state.tables.get(self.table_name, [])
        for col, val in self._eq.items():
            rows = [r for r in rows if r.get(col) == val]
        if self._ilike:
            col, val = self._ilike
            needle = val.lower()
            rows = [r for r in rows if str(r.get(col, "")).lower() == needle]
        if self._order_col:
            rows = sorted(rows, key=lambda r: r.get(self._order_col) or "", reverse=self._order_desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start:end + 1]
        return rows

    def execute(self):
        self.state.calls.append({
            "table": self.table_name,
            "op": (self._op[0] if self._op else "select"),
            "eq": dict(self._eq),
            "ilike": self._ilike,
            "range": self._range,
            "payload": (self._op[1] if self._op else None),
        })

        key = (self.table_name, self._op[0] if self._op else "select")
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
            # insert_returns override: simulate "insert succeeded but PostgREST
            # returned no representation" for a specific table.
            if self.table_name in self.state.insert_returns_empty:
                return _Resp([])
            self.state.tables.setdefault(self.table_name, []).extend(new_rows)
            return _Resp(new_rows)
        if op == "update":
            if self.table_name in self.state.update_returns_empty:
                return _Resp([])
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
        self.tables: Dict[str, list] = {k: [dict(row) for row in v] for k, v in (tables or {}).items()}
        self.calls: List[dict] = []
        self.fail_once: Dict[Tuple[str, str], str] = {}
        self.always_fail: Dict[str, str] = {}
        self.insert_returns_empty: set = set()
        self.update_returns_empty: set = set()


class FakeSupabase:
    """In-memory Supabase stand-in with real eq/ilike/order/range filtering and
    real insert/update/delete mutation, keyed by table name. Every execute()
    call is recorded in `.state.calls` for exact table/op/filter/payload
    assertions.

    tables: {table_name: [row_dict, ...]} initial fixture data.
    """

    def __init__(self, tables: Optional[Dict[str, list]] = None):
        self.state = _FakeState(tables)

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(name, self.state)

    def fail_once(self, table: str, op: str, message: str = "simulated failure"):
        self.state.fail_once[(table, op)] = message

    def always_fail(self, table: str, message: str = "simulated failure"):
        self.state.always_fail[table] = message
