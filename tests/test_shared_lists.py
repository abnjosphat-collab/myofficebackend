# tests/test_shared_lists.py — the PPE order list and saved quotations routers, called directly against a fake supabase client
# (the sanctioned recipe: see test_services_crud.py). Every Depends()-defaulted parameter is passed explicitly.
import pytest
from fastapi import HTTPException

from app.routers import ppe_order_list as ppe_mod
from app.routers import saved_quotations as quo_mod
from app.routers.ppe_order_list import AddLines, OrderLine, RemoveLines, add_order_lines, clear_order_list, list_order_lines, remove_order_lines
from app.routers.saved_quotations import SaveQuotation, delete_quotation, list_quotations, save_quotation


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, state, cfg):
        self.state, self.cfg = state, cfg
        self._op, self._payload, self._filters, self._kw = "select", None, [], {}

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def range(self, *a, **k): return self
    def eq(self, col, val): self._filters.append(("eq", col, val)); return self
    def neq(self, col, val): self._filters.append(("neq", col, val)); return self
    def in_(self, col, vals): self._filters.append(("in", col, list(vals))); return self
    def upsert(self, data, **kw): self._op, self._payload, self._kw = "upsert", data, kw; return self
    def delete(self): self._op = "delete"; return self

    def execute(self):
        self.state["calls"].append({"op": self._op, "payload": self._payload, "filters": list(self._filters), "kw": dict(self._kw)})
        if self.cfg.get("raise_op") == self._op:
            raise Exception(self.cfg.get("raise_msg", "boom"))
        if self._op == "upsert":
            return _Resp(self.cfg.get("upsert_return", self._payload if isinstance(self._payload, list) else [self._payload]))
        if self._op == "delete":
            return _Resp([])
        return _Resp(self.cfg.get("select_return", []))


class _Fake:
    def __init__(self, cfg):
        self.state, self.cfg = {"calls": []}, cfg

    def table(self, name):
        return _Query(self.state, self.cfg)


@pytest.fixture
def fake(monkeypatch):
    def _apply(cfg=None) -> _Fake:
        f = _Fake(cfg or {})
        monkeypatch.setattr(ppe_mod, "supabase", f)
        monkeypatch.setattr(quo_mod, "supabase", f)
        return f
    return _apply


USER = {"user_id": "u1", "email": "store@x.com", "role": "user"}
MANAGER = {"user_id": "m1", "email": "boss@x.com", "role": "manager"}
LINE = OrderLine(record_id="r1", employee_id="C1", employee_name="Ann", ppe_type="Boots", item_name="Safety boots", size="8", expiry_date="2026-11-01")


# ─── PPE order list ──────────────────────────────────────────────────────────────────

async def test_list_returns_each_entry_with_its_record_id_and_added_time(fake):
    fake({"select_return": [{"record_id": "r1", "entry": {"employee_name": "Ann", "size": "8"}, "added_at": "2026-10-05T08:00:00Z", "added_by": "a"}]})
    assert await list_order_lines() == [{"employee_name": "Ann", "size": "8", "record_id": "r1", "added_at": "2026-10-05T08:00:00Z"}]


async def test_adding_is_idempotent_by_record_and_stamps_who_added(fake):
    f = fake({"upsert_return": [{"record_id": "r1"}]})
    assert await add_order_lines(AddLines(entries=[LINE]), current_user=USER) == {"added": 1}
    call = f.state["calls"][0]
    assert call["kw"] == {"on_conflict": "record_id", "ignore_duplicates": True}
    assert call["payload"][0]["record_id"] == "r1" and call["payload"][0]["added_by"] == "store@x.com"
    assert call["payload"][0]["entry"]["ppe_type"] == "Boots"


async def test_adding_nothing_does_not_touch_the_database(fake):
    f = fake()
    assert await add_order_lines(AddLines(entries=[]), current_user=USER) == {"added": 0}
    assert f.state["calls"] == []


async def test_removing_deletes_only_the_named_records(fake):
    f = fake()
    assert await remove_order_lines(RemoveLines(record_ids=["r1", "r2"]), current_user=USER) == {"ok": True}
    assert f.state["calls"][0]["filters"] == [("in", "record_id", ["r1", "r2"])]


async def test_clearing_empties_the_list(fake):
    f = fake()
    assert await clear_order_list(current_user=USER) == {"ok": True}
    assert f.state["calls"][0]["op"] == "delete"


@pytest.mark.parametrize("op,call", [
    ("select", lambda: list_order_lines()),
    ("upsert", lambda: add_order_lines(AddLines(entries=[LINE]), current_user=USER)),
    ("delete", lambda: remove_order_lines(RemoveLines(record_ids=["r1"]), current_user=USER)),
    ("delete", lambda: clear_order_list(current_user=USER)),
])
async def test_database_errors_are_500_not_an_empty_success(fake, op, call):
    fake({"raise_op": op, "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await call()
    assert exc.value.status_code == 500 and "db down" in exc.value.detail


# ─── saved quotations ────────────────────────────────────────────────────────────────

async def test_quotations_list_shape(fake):
    fake({"select_return": [{"id": "QT-1", "saved_at": "2026-10-05T08:00:00Z", "saved_by": "a@x.com", "draft": {"number": "QT-1"}}]})
    assert await list_quotations() == [{"id": "QT-1", "savedAt": "2026-10-05T08:00:00Z", "savedBy": "a@x.com", "draft": {"number": "QT-1"}}]


async def test_saving_upserts_by_number_and_records_who(fake):
    f = fake()
    out = await save_quotation("QT-7", SaveQuotation(draft={"number": "QT-7"}), current_user=USER)
    call = f.state["calls"][0]
    assert call["kw"] == {"on_conflict": "id"}
    assert call["payload"]["id"] == "QT-7" and call["payload"]["saved_by"] == "store@x.com" and call["payload"]["saved_by_id"] == "u1"
    assert out["id"] == "QT-7"


@pytest.mark.parametrize("quotation_id,draft", [("  ", {"a": 1}), ("QT-9", {"text": "x" * 500_000})])
async def test_saving_refuses_a_blank_number_or_an_oversized_draft(fake, quotation_id, draft):
    f = fake()
    with pytest.raises(HTTPException) as exc:
        await save_quotation(quotation_id, SaveQuotation(draft=draft), current_user=USER)
    assert exc.value.status_code == 422 and f.state["calls"] == []


async def test_the_person_who_saved_it_can_delete_it(fake):
    f = fake({"select_return": [{"id": "QT-1", "saved_by_id": "u1"}]})
    assert await delete_quotation("QT-1", current_user=USER) == {"ok": True}
    assert any(c["op"] == "delete" for c in f.state["calls"])


async def test_someone_else_cannot_delete_it_but_a_manager_can(fake):
    f = fake({"select_return": [{"id": "QT-1", "saved_by_id": "someone-else"}]})
    with pytest.raises(HTTPException) as exc:
        await delete_quotation("QT-1", current_user=USER)
    assert exc.value.status_code == 403 and not any(c["op"] == "delete" for c in f.state["calls"])
    assert await delete_quotation("QT-1", current_user=MANAGER) == {"ok": True}
    assert any(c["op"] == "delete" for c in f.state["calls"])


async def test_deleting_one_that_is_already_gone_is_fine(fake):
    f = fake({"select_return": []})
    assert await delete_quotation("QT-404", current_user=USER) == {"ok": True}
    assert not any(c["op"] == "delete" for c in f.state["calls"])
