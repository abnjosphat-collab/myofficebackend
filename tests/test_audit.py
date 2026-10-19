# tests/test_audit.py — every write to a payroll or people table is recorded, with who did it taken from the session.
# The recorder is a wrapper around the shared Supabase client; these tests use a fake "real" client and check what it
# is asked to write, that reads and other tables are untouched, and that a failing log never breaks the real write.
import pytest

from app import audit
from app.audit import AuditedClient, AuditState, decision_stamp, has_columns, set_actor


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

    def in_(self, col, vals):
        self.filters.append((col, list(vals)))
        return self

    def limit(self, n):
        return self

    def execute(self):
        self.real.calls.append((self.table, self.op, self.payload, list(self.filters)))
        if self.table == "audit_log":
            if self.real.audit_error:
                raise Exception(self.real.audit_error)
            return Resp(self.payload)
        if self.real.write_error and self.op != "select":
            raise Exception(self.real.write_error)
        return Resp(self.real.returns.get((self.table, self.op), []))


class FakeReal:
    def __init__(self, returns=None, audit_error=None, write_error=None):
        self.calls, self.returns, self.audit_error, self.write_error = [], returns or {}, audit_error, write_error

    def table(self, name):
        return FakeBuilder(self, name)

    def audit_rows(self):
        return [row for call in self.calls if call[0] == "audit_log" for row in call[2]]


@pytest.fixture(autouse=True)
def clean_state():
    AuditState.written = AuditState.failures = 0
    AuditState.table_missing = False
    AuditState.last_error = None
    set_actor({"user_id": "u-1", "email": "elias.mwazha@dallaglio.co.zw", "name": "Elias Mwazha", "role": "manager"})
    audit._COLUMN_PROBE.clear()
    yield
    set_actor(None)


def test_a_create_records_the_actor_and_the_whole_row():
    real = FakeReal({("overtime", "insert"): [{"id": 5, "employee_name": "A", "approval_signature": "data:image/png;base64,AAAA"}]})
    AuditedClient(real).table("overtime").insert({"employee_name": "A"}).execute()
    [entry] = real.audit_rows()
    assert entry["action"] == "create" and entry["table_name"] == "overtime" and entry["record_id"] == "5"
    assert entry["actor_name"] == "Elias Mwazha" and entry["actor_email"] == "elias.mwazha@dallaglio.co.zw" and entry["actor_role"] == "manager"
    assert entry["snapshot"]["employee_name"] == "A"
    assert "approval_signature" not in entry["snapshot"]  # the signature image is never copied into the log


def test_an_update_records_only_what_changed_and_names_an_approval():
    real = FakeReal({("overtime", "update"): [{"id": 7}, {"id": 8}]})
    AuditedClient(real).table("overtime").update({"status": "approved", "approved_by": "Elias Mwazha"}).in_("id", [7, 8]).execute()
    entries = real.audit_rows()
    assert [e["record_id"] for e in entries] == ["7", "8"]
    assert all(e["action"] == "approve" and e["changes"] == {"status": "approved", "approved_by": "Elias Mwazha"} for e in entries)
    assert all("snapshot" not in e for e in entries)


def test_a_delete_keeps_the_whole_row_so_it_can_be_put_back():
    real = FakeReal({("leaves", "delete"): [{"id": 3, "employee_name": "B", "start_date": "2026-03-04"}]})
    AuditedClient(real).table("leaves").delete().eq("id", 3).execute()
    [entry] = real.audit_rows()
    assert entry["action"] == "delete" and entry["snapshot"] == {"id": 3, "employee_name": "B", "start_date": "2026-03-04"}


def test_reads_and_other_tables_are_not_recorded():
    real = FakeReal({("work_orders", "insert"): [{"id": 1}], ("overtime", "select"): [{"id": 1}]})
    client = AuditedClient(real)
    client.table("work_orders").insert({"x": 1}).execute()
    client.table("overtime").select("*").eq("id", 1).execute()
    assert real.audit_rows() == []


def test_an_update_that_changes_nothing_records_nothing():
    real = FakeReal({("overtime", "update"): []})
    AuditedClient(real).table("overtime").update({"reason": "x"}).eq("id", 99).execute()
    assert real.audit_rows() == []


def test_no_signed_in_person_is_recorded_as_system():
    set_actor(None)
    real = FakeReal({("overtime", "insert"): [{"id": 2}]})
    AuditedClient(real).table("overtime").insert({"a": 1}).execute()
    assert real.audit_rows()[0]["actor_email"] == "system"


def test_long_text_is_shortened():
    real = FakeReal({("overtime", "insert"): [{"id": 4, "notes": "x" * 5000}]})
    AuditedClient(real).table("overtime").insert({"a": 1}).execute()
    assert len(real.audit_rows()[0]["snapshot"]["notes"]) < 600


def test_a_failing_audit_never_breaks_the_write_and_is_counted():
    real = FakeReal({("overtime", "insert"): [{"id": 9}]}, audit_error="connection reset")
    response = AuditedClient(real).table("overtime").insert({"a": 1}).execute()
    assert response.data == [{"id": 9}]  # the person's action went through
    assert AuditState.failures == 1
    assert audit.audit_status()["status"] == "failing"


def test_a_missing_audit_table_is_reported_not_hidden():
    real = FakeReal({("overtime", "insert"): [{"id": 9}]}, audit_error='relation "public.audit_log" does not exist (42P01)')
    AuditedClient(real).table("overtime").insert({"a": 1}).execute()
    assert AuditState.table_missing is True and audit.audit_status()["status"] == "missing"


def test_a_failing_write_is_not_recorded_and_still_raises():
    real = FakeReal(write_error="duplicate key")
    with pytest.raises(Exception, match="duplicate key"):
        AuditedClient(real).table("overtime").insert({"a": 1}).execute()
    assert real.audit_rows() == []


def test_decision_stamp_comes_from_the_session():
    approved = decision_stamp("approved", {"name": "Edson Mavhondo", "email": "e@x"})
    assert approved["approved_by"] == "Edson Mavhondo" and approved["approved_at"]
    assert decision_stamp("rejected", {"email": "e@x"})["rejected_by"] == "e@x"
    assert decision_stamp("pending", {"name": "x"}) == {}
    assert decision_stamp("approved", None)["approved_by"] == "system"


def test_has_columns_remembers_yes_and_rechecks_no(monkeypatch):
    class Probe:
        def __init__(self, ok):
            self.ok, self.calls = ok, 0

        def table(self, name):
            return self

        def select(self, cols):
            return self

        def limit(self, n):
            return self

        def execute(self):
            self.calls += 1
            if not self.ok:
                raise Exception('column leaves.approved_by does not exist (42703)')
            return Resp([])

    missing = Probe(False)
    assert has_columns(missing, "leaves", ["approved_by"]) is False
    assert has_columns(missing, "leaves", ["approved_by"]) is False and missing.calls == 1  # a no is held for a minute
    audit._COLUMN_PROBE.clear()
    present = Probe(True)
    assert has_columns(present, "leaves", ["approved_by"]) is True
    assert has_columns(present, "leaves", ["approved_by"]) is True and present.calls == 1  # a yes is held
    audit._COLUMN_PROBE.clear()


def test_a_network_failure_is_not_remembered_as_a_missing_column():
    class Flaky:
        def table(self, name):
            return self

        def select(self, cols):
            return self

        def limit(self, n):
            return self

        def execute(self):
            raise Exception("connection reset by peer")

    assert has_columns(Flaky(), "leaves", ["approved_by"]) is False
    assert audit._COLUMN_PROBE == {}
