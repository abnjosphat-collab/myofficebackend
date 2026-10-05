# tests/test_stock_levels.py — issuing stock takes it off the spare's quantity; deleting an issue puts it back; editing adjusts by the
# difference. A small in-memory fake of the two tables (stock_issues, spares) with just the query calls the code makes.
import pytest
from fastapi import HTTPException

import app.routers.issues as issues_mod
import app.stock_levels as stock_mod
from app.routers.issues import StockIssueCreate, StockIssueUpdate, _create_issue, _delete_issue, _update_issue
from app.stock_levels import adjust_stock, item_deltas


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self._op, self._payload, self._filters = "select", None, []

    def select(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, col, val): self._filters.append((col, val)); return self
    def insert(self, data): self._op, self._payload = "insert", data; return self
    def update(self, data): self._op, self._payload = "update", data; return self
    def delete(self): self._op = "delete"; return self

    def _match(self, row): return all(row.get(c) == v for c, v in self._filters)

    def execute(self):
        rows = self.db.tables[self.table_name]
        if self._op == "insert":
            row = {"id": len(rows) + 1, **self._payload}
            rows.append(row)
            return _Resp([dict(row)])
        hit = [r for r in rows if self._match(r)]
        if self._op == "update":
            for r in hit:
                r.update(self._payload)
            return _Resp([dict(r) for r in hit])
        if self._op == "delete":
            self.db.tables[self.table_name] = [r for r in rows if not self._match(r)]
            return _Resp([])
        return _Resp([dict(r) for r in hit])


class _Db:
    def __init__(self, spares, issues=None):
        self.tables = {"spares": spares, "stock_issues": issues or []}
        self.fail_spares_update = False

    def table(self, name):
        q = _Query(self, name)
        if name == "spares" and self.fail_spares_update:
            def boom(data):
                raise RuntimeError("db down")
            q.update = boom  # type: ignore[method-assign]
        return q


@pytest.fixture
def db(monkeypatch):
    def _make(spares, issues=None):
        d = _Db(spares, issues)
        monkeypatch.setattr(issues_mod, "supabase", d)
        monkeypatch.setattr(stock_mod, "supabase", d)
        return d
    return _make


def qty(d, code):
    return next(s["current_quantity"] for s in d.tables["spares"] if s["stock_code"] == code)


def issue(items, **over):
    return StockIssueCreate(recipient_name="J. Moyo", items=items, **over)


PARTS = lambda: [{"id": 1, "stock_code": "A100", "current_quantity": 10}, {"id": 2, "stock_code": "B200", "current_quantity": 3}]


def test_deltas_add_up_per_code_and_skip_lines_without_a_code_or_quantity():
    assert item_deltas([{"stock_code": "A", "qty": 2}, {"stock_code": " A ", "qty": 1.5}, {"stock_code": "", "qty": 4}, {"stock_code": "B", "qty": 0}, {"description": "x", "qty": 1}]) == {"A": 3.5}


async def test_issuing_takes_the_quantity_off_each_spare(db):
    d = db(PARTS())
    out = await _create_issue(issue([{"stock_code": "A100", "description": "Seal", "qty": 4}, {"stock_code": "B200", "description": "Belt", "qty": 1}]))
    assert qty(d, "A100") == 6 and qty(d, "B200") == 2
    assert out["id"] == 1 and out["stock_warnings"] == []
    assert {u["stock_code"]: (u["before"], u["after"]) for u in out["stock"]} == {"A100": (10, 6), "B200": (3, 2)}


async def test_issuing_more_than_is_in_stock_goes_to_zero_and_says_so(db):
    d = db(PARTS())
    out = await _create_issue(issue([{"stock_code": "B200", "description": "Belt", "qty": 5}]))
    assert qty(d, "B200") == 0
    assert out["stock"][0]["short_by"] == 2
    assert "only 3 in stock" in out["stock_warnings"][0] and "2 short" in out["stock_warnings"][0]


async def test_an_item_with_no_code_or_an_unknown_code_is_recorded_without_moving_stock(db):
    d = db(PARTS())
    out = await _create_issue(issue([{"description": "Loose rag", "qty": 2}, {"stock_code": "NOPE", "description": "Mystery", "qty": 1}]))
    assert qty(d, "A100") == 10 and qty(d, "B200") == 3
    assert out["id"] == 1 and len(d.tables["stock_issues"]) == 1
    assert any("NOPE" in w and "not found" in w for w in out["stock_warnings"])


async def test_a_stock_failure_never_loses_the_issue_record(db):
    d = db(PARTS())
    d.fail_spares_update = True
    out = await _create_issue(issue([{"stock_code": "A100", "description": "Seal", "qty": 1}]))
    assert len(d.tables["stock_issues"]) == 1
    assert any("could not be updated" in w for w in out["stock_warnings"])


async def test_deleting_an_issue_puts_the_stock_back(db):
    d = db(PARTS(), [{"id": 7, "recipient_name": "J", "items": [{"stock_code": "A100", "description": "Seal", "qty": 4}]}])
    d.tables["spares"][0]["current_quantity"] = 6
    out = await _delete_issue(7, current_user={"role": "manager"})
    assert out["ok"] is True and qty(d, "A100") == 10 and d.tables["stock_issues"] == []


async def test_editing_an_issue_moves_only_the_difference(db):
    d = db(PARTS(), [{"id": 7, "recipient_name": "J", "items": [{"stock_code": "A100", "description": "Seal", "qty": 4}, {"stock_code": "B200", "description": "Belt", "qty": 1}]}])
    d.tables["spares"][0]["current_quantity"] = 6
    d.tables["spares"][1]["current_quantity"] = 2
    out = await _update_issue(7, StockIssueUpdate(items=[{"stock_code": "A100", "description": "Seal", "qty": 6}]))
    assert qty(d, "A100") == 4   # two more taken off
    assert qty(d, "B200") == 3   # the belt line was removed: one put back
    assert out["stock_warnings"] == []


async def test_editing_something_other_than_the_items_leaves_stock_alone(db):
    d = db(PARTS(), [{"id": 7, "recipient_name": "J", "items": [{"stock_code": "A100", "description": "Seal", "qty": 4}]}])
    await _update_issue(7, StockIssueUpdate(notes="collected by supervisor"))
    assert qty(d, "A100") == 10


async def test_editing_an_issue_that_is_not_there_is_404(db):
    db(PARTS())
    with pytest.raises(HTTPException) as exc:
        await _update_issue(99, StockIssueUpdate(notes="x"))
    assert exc.value.status_code == 404


def test_fractional_quantities_round_to_whole_units(db):
    db([{"id": 1, "stock_code": "CABLE", "current_quantity": 10}])
    out = adjust_stock({"CABLE": 2.5}, issue=True)
    assert out["stock"][0]["after"] in (7, 8)
