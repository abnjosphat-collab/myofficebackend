# tests/test_audit_router.py — the change history is readable by managers and above, only for audited tables, read-only.
import pytest
from fastapi import HTTPException

from app.routers import audit as audit_router


class Resp:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, store):
        self.store = store

    def select(self, *a):
        return self

    def eq(self, col, val):
        self.store.setdefault("filters", []).append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self.store["limit"] = n
        return self

    def execute(self):
        return Resp([{"id": 1, "action": "approve"}])


class Fake:
    def __init__(self):
        self.store = {}

    def table(self, name):
        self.store["table"] = name
        return Q(self.store)


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    monkeypatch.setattr(audit_router, "supabase", f)
    return f


async def test_history_of_one_record(fake):
    out = await audit_router.list_audit_entries(table="overtime", record_id="7", limit=50, current_user={"role": "manager"})
    assert out == [{"id": 1, "action": "approve"}]
    assert fake.store["table"] == "audit_log" and ("table_name", "overtime") in fake.store["filters"] and ("record_id", "7") in fake.store["filters"]
    assert fake.store["limit"] == 50


async def test_an_unaudited_table_is_refused(fake):
    with pytest.raises(HTTPException) as exc:
        await audit_router.list_audit_entries(table="work_orders", record_id=None, limit=10, current_user={"role": "manager"})
    assert exc.value.status_code == 400


async def test_status_reports_the_recorder_state():
    assert (await audit_router.audit_log_status(current_user={"role": "manager"}))["status"] in {"ok", "failing", "missing"}
