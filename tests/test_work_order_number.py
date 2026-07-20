# tests/test_work_order_number.py — server-side work-order-number generation + the
# unique-violation retry that makes concurrent creates race-safe (backed by the
# uq_work_orders_number index). Pure/mocked; no network.

import pytest
import app.routers.maintenance as m


class _SelectResp:
    def __init__(self, rows):
        self.data = rows


class _FakeTable:
    """Returns `existing` rows on select().execute(); on insert().execute() raises a
    unique-violation the first `fail_times` calls, then succeeds."""
    def __init__(self, state):
        self.state = state

    def select(self, *a, **k):
        self.state["mode"] = "select"
        return self

    def insert(self, data):
        self.state["mode"] = "insert"
        self.state["last_insert"] = data
        return self

    def execute(self):
        if self.state["mode"] == "select":
            return _SelectResp(self.state["existing"])
        self.state["attempts"] += 1
        if self.state["attempts"] <= self.state["fail_times"]:
            raise Exception('duplicate key value violates unique constraint "uq_work_orders_number"')
        return _SelectResp([dict(self.state["last_insert"], id=1)])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        return _FakeTable(self.state)


# ─── _generate_wo_number ──────────────────────────────────────────────────────

def _patch(monkeypatch, existing, fail_times=0):
    state = {"existing": existing, "attempts": 0, "fail_times": fail_times, "mode": None, "last_insert": None}
    monkeypatch.setattr(m, "supabase", _FakeSupabase(state))
    return state


def test_generate_uses_max_trailing_digits(monkeypatch):
    _patch(monkeypatch, [{"work_order_number": "WO-00099"},
                         {"work_order_number": "WO-107846"},
                         {"work_order_number": None}])
    assert m._generate_wo_number() == "WO-107847"
    assert m._generate_wo_number(offset=2) == "WO-107849"


def test_generate_from_empty_table(monkeypatch):
    _patch(monkeypatch, [])
    assert m._generate_wo_number() == "WO-00001"


def test_generate_pads_small_numbers(monkeypatch):
    _patch(monkeypatch, [{"work_order_number": "WO-00007"}])
    assert m._generate_wo_number() == "WO-00008"


# ─── _is_unique_violation ─────────────────────────────────────────────────────

@pytest.mark.parametrize("msg", [
    "duplicate key value violates unique constraint \"uq_work_orders_number\"",
    "postgrest error code 23505",
    "UNIQUE constraint failed",
])
def test_detects_unique_violation(msg):
    assert m._is_unique_violation(Exception(msg)) is True


def test_ignores_other_errors():
    assert m._is_unique_violation(Exception("connection reset")) is False
    assert m._is_unique_violation(Exception("null value in column")) is False
