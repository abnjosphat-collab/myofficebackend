# tests/test_recently_deleted.py — every delete keeps a full, untruncated copy of the
# row (signatures included) with who/when in `deleted_records`; managers list and
# restore through /api/recently-deleted. Documents keep their own trash.
import inspect

import pytest
from fastapi import HTTPException

from app import audit
from app.audit import AuditedClient, AuditState, TrashState, set_actor


class Resp:
    def __init__(self, data):
        self.data = data


class FakeBuilder:
    def __init__(self, real, table):
        self.real, self.table = real, table
        self.op, self.payload, self.filters = "select", None, []

    def insert(self, rows, **kw):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, **kw):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, values, **kw):
        self.op, self.payload = "update", values
        return self

    def delete(self, **kw):
        self.op = "delete"
        return self

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def or_(self, expr):
        self.filters.append(("or", expr))
        return self

    def is_(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def offset(self, n):
        return self

    def execute(self):
        self.real.calls.append((self.table, self.op, self.payload, list(self.filters)))
        key = (self.table, self.op)
        if key in self.real.errors:
            err = self.real.errors[key]
            raise err if isinstance(err, Exception) else Exception(err)
        return Resp(self.real.returns.get(key, []))


class FakeReal:
    def __init__(self, returns=None, errors=None):
        self.calls, self.returns, self.errors = [], returns or {}, errors or {}

    def table(self, name):
        return FakeBuilder(self, name)

    def rows_for(self, table):
        return [row for call in self.calls if call[0] == table for row in (call[2] or [])]


@pytest.fixture(autouse=True)
def clean_state():
    AuditState.written = AuditState.failures = 0
    AuditState.table_missing = False
    AuditState.last_error = None
    TrashState.written = TrashState.failures = 0
    TrashState.table_missing = False
    TrashState.last_error = None
    set_actor({"user_id": "u-1", "email": "amina@example.com", "name": "Amina", "role": "manager"})
    yield
    set_actor(None)


def trash_rows(real):
    return real.rows_for("deleted_records")


# --- capture: every delete keeps a full copy ---

def test_a_delete_on_a_plain_table_keeps_the_full_row():
    row = {"id": 44, "work_order_number": "WO-1", "signature": "data:image/png;base64,AAAA", "notes": "x" * 5000}
    real = FakeReal({("work_orders", "delete"): [row]})
    AuditedClient(real).table("work_orders").delete().eq("id", 44).execute()
    [entry] = trash_rows(real)
    assert entry["table_name"] == "work_orders" and entry["record_id"] == "44"
    assert entry["snapshot"] == row  # signatures kept, long text untruncated
    assert entry["deleted_by_email"] == "amina@example.com" and entry["deleted_by_name"] == "Amina"


def test_a_delete_on_an_audited_table_feeds_both_logs():
    row = {"id": 3, "employee_name": "B", "approval_signature": "sig", "notes": "y" * 5000}
    real = FakeReal({("leaves", "delete"): [row]})
    AuditedClient(real).table("leaves").delete().eq("id", 3).execute()
    [audited] = real.rows_for("audit_log")
    assert "approval_signature" not in audited["snapshot"]  # the audit log stays small
    [trashed] = trash_rows(real)
    assert trashed["snapshot"] == row  # the trash keeps everything, for restore


def test_non_delete_writes_on_plain_tables_are_not_trashed():
    real = FakeReal({("work_orders", "insert"): [{"id": 1}], ("work_orders", "update"): [{"id": 1}]})
    client = AuditedClient(real)
    client.table("work_orders").insert({"x": 1}).execute()
    client.table("work_orders").update({"x": 2}).eq("id", 1).execute()
    assert trash_rows(real) == []


@pytest.mark.parametrize("table", ["documents", "document_folders", "audit_log", "deleted_records"])
def test_excluded_tables_are_never_trashed(table):
    real = FakeReal({(table, "delete"): [{"id": 1, "title": "t"}]})
    AuditedClient(real).table(table).delete().eq("id", 1).execute()
    assert trash_rows(real) == []


def test_a_failing_trash_never_breaks_the_delete_and_is_counted():
    real = FakeReal(
        {("work_orders", "delete"): [{"id": 9}]},
        errors={("deleted_records", "insert"): "connection reset"},
    )
    response = AuditedClient(real).table("work_orders").delete().eq("id", 9).execute()
    assert response.data == [{"id": 9}]  # the person's delete went through
    assert TrashState.failures == 1
    assert audit.trash_status()["status"] == "failing"


def test_a_missing_trash_table_is_reported_not_hidden():
    real = FakeReal(
        {("work_orders", "delete"): [{"id": 9}]},
        errors={("deleted_records", "insert"): 'relation "public.deleted_records" does not exist (42P01)'},
    )
    AuditedClient(real).table("work_orders").delete().eq("id", 9).execute()
    assert TrashState.table_missing is True
    assert audit.trash_status()["status"] == "missing"


# --- the list + restore endpoints ---

def _router():
    from app.routers import recently_deleted
    return recently_deleted


def _manager():
    return {"user_id": "u-1", "email": "amina@example.com", "name": "Amina", "role": "manager"}


def test_endpoints_are_manager_only():
    mod = _router()
    for fn in (mod.list_recently_deleted, mod.restore_deleted):
        default = inspect.signature(fn).parameters["current_user"].default
        assert getattr(default, "dependency", None) is not None


async def test_list_returns_rows_and_applies_filters(monkeypatch):
    mod = _router()
    wanted = [{"id": "t1", "table_name": "leaves"}]
    real = FakeReal({("deleted_records", "select"): wanted})
    monkeypatch.setattr(mod, "supabase", real)
    out = await mod.list_recently_deleted(q="amina", table="leaves", limit=10, offset=0, current_user=_manager())
    assert out == wanted
    (table, _op, _payload, filters) = real.calls[0]
    assert table == "deleted_records"
    assert ("table_name", "leaves") in filters
    assert any(name == "or" and "amina" in expr for name, expr in filters)


async def test_list_search_cannot_inject_postgrest_operators(monkeypatch):
    mod = _router()
    real = FakeReal({("deleted_records", "select"): []})
    monkeypatch.setattr(mod, "supabase", real)
    await mod.list_recently_deleted(q="a,b.(c)", table=None, limit=10, offset=0, current_user=_manager())
    expr = [e for name, e in real.calls[0][3] if name == "or"][0]
    assert expr == "table_name.ilike.*abc*,record_id.ilike.*abc*,deleted_by_email.ilike.*abc*,deleted_by_name.ilike.*abc*"


async def test_restore_reinserts_the_identical_row_and_marks_it(monkeypatch):
    mod = _router()
    snapshot = {"id": 44, "work_order_number": "WO-1", "notes": "keep me"}
    entry = {"id": "t1", "table_name": "work_orders", "record_id": "44", "snapshot": snapshot, "restored_at": None}
    real = FakeReal({
        ("deleted_records", "select"): [entry],
        ("work_orders", "select"): [],
        ("work_orders", "insert"): [snapshot],
        ("deleted_records", "update"): [{"id": "t1"}],
    })
    monkeypatch.setattr(mod, "supabase", real)
    out = await mod.restore_deleted("t1", current_user=_manager())
    assert out["table_name"] == "work_orders" and out["record_id"] == "44"
    inserts = [c for c in real.calls if c[0] == "work_orders" and c[1] == "insert"]
    assert len(inserts) == 1 and inserts[0][2] == snapshot
    updates = [c for c in real.calls if c[0] == "deleted_records" and c[1] == "update"]
    assert len(updates) == 1 and updates[0][2]["restored_by"] == "amina@example.com"


async def test_restore_of_a_missing_entry_is_404(monkeypatch):
    mod = _router()
    real = FakeReal({("deleted_records", "select"): []})
    monkeypatch.setattr(mod, "supabase", real)
    with pytest.raises(HTTPException) as exc:
        await mod.restore_deleted("gone", current_user=_manager())
    assert exc.value.status_code == 404


async def test_restore_of_an_already_restored_entry_is_409(monkeypatch):
    mod = _router()
    entry = {"id": "t1", "table_name": "leaves", "record_id": "3", "snapshot": {"id": 3}, "restored_at": "2026-10-20T00:00:00+00:00"}
    real = FakeReal({("deleted_records", "select"): [entry]})
    monkeypatch.setattr(mod, "supabase", real)
    with pytest.raises(HTTPException) as exc:
        await mod.restore_deleted("t1", current_user=_manager())
    assert exc.value.status_code == 409
    assert not [c for c in real.calls if c[1] == "insert"]


async def test_restore_clash_with_a_live_row_is_409_without_inserting(monkeypatch):
    mod = _router()
    snapshot = {"id": 3, "employee_name": "B"}
    entry = {"id": "t1", "table_name": "leaves", "record_id": "3", "snapshot": snapshot, "restored_at": None}
    real = FakeReal({
        ("deleted_records", "select"): [entry],
        ("leaves", "select"): [{"id": 3}],
    })
    monkeypatch.setattr(mod, "supabase", real)
    with pytest.raises(HTTPException) as exc:
        await mod.restore_deleted("t1", current_user=_manager())
    assert exc.value.status_code == 409 and "already exists" in exc.value.detail
    assert not [c for c in real.calls if c[1] == "insert"]


async def test_restore_of_a_documents_entry_is_refused(monkeypatch):
    mod = _router()
    entry = {"id": "t1", "table_name": "documents", "record_id": "d1", "snapshot": {"id": "d1"}, "restored_at": None}
    real = FakeReal({("deleted_records", "select"): [entry]})
    monkeypatch.setattr(mod, "supabase", real)
    with pytest.raises(HTTPException) as exc:
        await mod.restore_deleted("t1", current_user=_manager())
    assert exc.value.status_code == 400


async def test_restore_unique_race_returns_409_not_500(monkeypatch):
    mod = _router()
    snapshot = {"id": 3, "employee_name": "B"}
    entry = {"id": "t1", "table_name": "leaves", "record_id": "3", "snapshot": snapshot, "restored_at": None}

    class FakeAPIError(Exception):
        code = "23505"
        message = 'duplicate key value violates unique constraint "leaves_pkey"'
        details = ""

    real = FakeReal(
        {("deleted_records", "select"): [entry], ("leaves", "select"): []},
        errors={("leaves", "insert"): FakeAPIError('duplicate key value violates unique constraint "leaves_pkey"')},
    )
    monkeypatch.setattr(mod, "supabase", real)
    with pytest.raises(HTTPException) as exc:
        await mod.restore_deleted("t1", current_user=_manager())
    assert exc.value.status_code == 409
