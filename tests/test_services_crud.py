# tests/test_services_crud.py — the CRUD route handlers behind services.py
# (list_services, create_service, update_service, delete_service). services.py was 44%
# covered with these handlers entirely untested — only the OCR regex parser had tests
# (see test_services_ocr_parsing.py, read first for the "same-line separator" bug
# context). Uses the sanctioned "call the route coroutine directly against a fake
# supabase client" recipe (see test_documents_folder_rename.py). Every Query()/Form()-
# defaulted parameter is passed explicitly — calling a route coroutine directly bypasses
# FastAPI's Depends()/Query() resolution.

from datetime import date

import pytest
from fastapi import HTTPException

from app.routers import services as services_mod
from app.routers.services import ServiceIn, list_services, create_service, update_service, delete_service


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table_name, state, cfg):
        self.table_name = table_name
        self.state = state
        self.cfg = cfg
        self._op = "select"
        self._filters = []
        self._payload = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def execute(self):
        self.state.setdefault("calls", []).append(
            {"table": self.table_name, "op": self._op, "filters": list(self._filters), "payload": self._payload}
        )
        if self.cfg.get("raise_op") == self._op:
            raise Exception(self.cfg.get("raise_msg", "boom"))
        if self._op == "insert":
            return _Resp(self.cfg.get("insert_return"))
        if self._op == "update":
            return _Resp(self.cfg.get("update_return"))
        if self._op == "delete":
            return _Resp(self.cfg.get("delete_return", [{"id": "s1"}]))
        return _Resp(self.cfg.get("select_return"))


class _FakeSupabase:
    def __init__(self, cfg):
        self.state = {"calls": []}
        self.cfg = cfg

    def table(self, name):
        assert name == "services"
        return _Query(name, self.state, self.cfg)


@pytest.fixture
def patch_supabase(monkeypatch):
    def _apply(cfg: dict) -> _FakeSupabase:
        fake = _FakeSupabase(cfg)
        monkeypatch.setattr(services_mod, "supabase", fake)
        return fake
    return _apply


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
MANAGER_USER = {"user_id": "m1", "email": "m1@x.com", "role": "manager"}


# ─── list_services ───────────────────────────────────────────────────────────────────

async def test_list_services_returns_rows(patch_supabase):
    patch_supabase({"select_return": [{"id": "s1", "supplier": "Acme"}]})
    result = await list_services()
    assert result == [{"id": "s1", "supplier": "Acme"}]


async def test_list_services_empty_is_empty_list_not_none(patch_supabase):
    patch_supabase({"select_return": None})
    assert await list_services() == []


async def test_list_services_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "select", "raise_msg": "db down"})
    with pytest.raises(HTTPException) as exc:
        await list_services()
    assert exc.value.status_code == 500
    assert "db down" in exc.value.detail


# ─── create_service ──────────────────────────────────────────────────────────────────

async def test_create_service_serializes_dates_and_stamps_timestamps(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": "s1", "supplier": "Acme Pumps"}]})
    body = ServiceIn(supplier="Acme Pumps", date=date(2024, 3, 15), amount="R 500.00")

    result = await create_service(body, current_user=CURRENT_USER)

    assert result == {"id": "s1", "supplier": "Acme Pumps"}
    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["date"] == "2024-03-15"  # date object -> ISO string, not a date object
    assert payload["supplier"] == "Acme Pumps"
    assert "created_at" in payload and "updated_at" in payload
    assert payload["created_at"] == payload["updated_at"]  # both stamped at creation


async def test_create_service_none_date_stays_none(patch_supabase):
    fake = patch_supabase({"insert_return": [{"id": "s1"}]})
    await create_service(ServiceIn(), current_user=CURRENT_USER)
    payload = next(c for c in fake.state["calls"] if c["op"] == "insert")["payload"]
    assert payload["date"] is None


async def test_create_service_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "insert", "raise_msg": "insert failed"})
    with pytest.raises(HTTPException) as exc:
        await create_service(ServiceIn(supplier="X"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "insert failed" in exc.value.detail


# ─── update_service ──────────────────────────────────────────────────────────────────

async def test_update_service_is_a_full_replace_including_unset_fields(patch_supabase):
    # ServiceIn's fields all carry defaults (it's a PUT, not a PATCH) and update_service
    # calls model_dump(exclude_none=False) — every field is sent on every update, not
    # just the ones the caller explicitly changed.
    fake = patch_supabase({"update_return": [{"id": "s1", "supplier": "New Supplier"}]})

    result = await update_service("s1", ServiceIn(supplier="New Supplier"), current_user=CURRENT_USER)

    assert result == {"id": "s1", "supplier": "New Supplier"}
    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["supplier"] == "New Supplier"
    assert payload["description"] == ""  # untouched default, still explicitly sent
    assert payload["planning_signed"] is False
    assert "updated_at" in payload
    update_call = next(c for c in fake.state["calls"] if c["op"] == "update")
    assert ("id", "s1") in update_call["filters"]


async def test_update_service_serializes_stage_sign_off_dates(patch_supabase):
    fake = patch_supabase({"update_return": [{"id": "s1"}]})
    await update_service(
        "s1",
        ServiceIn(planning_signed=True, planning_signed_by="Jo", planning_signed_date=date(2024, 5, 1)),
        current_user=CURRENT_USER,
    )
    payload = next(c for c in fake.state["calls"] if c["op"] == "update")["payload"]
    assert payload["planning_signed_date"] == "2024-05-01"
    assert payload["planning_signed"] is True


async def test_update_service_not_found_is_404(patch_supabase):
    patch_supabase({"update_return": []})
    with pytest.raises(HTTPException) as exc:
        await update_service("missing", ServiceIn(supplier="X"), current_user=CURRENT_USER)
    assert exc.value.status_code == 404


async def test_update_service_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "update", "raise_msg": "update failed"})
    with pytest.raises(HTTPException) as exc:
        await update_service("s1", ServiceIn(supplier="X"), current_user=CURRENT_USER)
    assert exc.value.status_code == 500
    assert "update failed" in exc.value.detail


# ─── delete_service ──────────────────────────────────────────────────────────────────

async def test_delete_service_happy_path(patch_supabase):
    fake = patch_supabase({})
    result = await delete_service("s1", current_user=MANAGER_USER)
    assert result == {"ok": True}
    delete_call = next(c for c in fake.state["calls"] if c["op"] == "delete")
    assert ("id", "s1") in delete_call["filters"]


async def test_delete_service_db_error_is_500(patch_supabase):
    patch_supabase({"raise_op": "delete", "raise_msg": "delete failed"})
    with pytest.raises(HTTPException) as exc:
        await delete_service("s1", current_user=MANAGER_USER)
    assert exc.value.status_code == 500
    assert "delete failed" in exc.value.detail
