# tests/test_training_endpoints.py — the training register's route handlers against an in-memory stand-in
# for the `training_certifications` table and the certificate storage bucket. Until 2026-10-09 the router
# kept a module-level mock list; these tests now pin the database-backed behaviour: records persist through
# the table, status is computed on read, a certificate file is really stored (and removed with its record),
# and a failure is reported instead of being shown as an empty register.

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.routers import training as training_mod
from tests._table_fake import TableFake
from app.routers.training import (
    create_new_certification, delete_certification, get_all_certifications, get_certification,
    get_compliance_rate, get_due_refreshers, get_employee_certifications, get_expiring_certifications,
    get_training_stats, update_certification,
)

TODAY = date.today()
USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
MANAGER = {"user_id": "m1", "email": "m1@x.com", "role": "manager"}


class _Upload:
    def __init__(self, filename, content=b"%PDF-1.4 certificate", content_type="application/pdf"):
        self.filename, self._content, self.content_type = filename, content, content_type

    async def read(self):
        return self._content


@pytest.fixture
def db(monkeypatch):
    fake = TableFake()
    monkeypatch.setattr(training_mod, "supabase", fake)
    return fake


def run(coro):
    return asyncio.run(coro)


def add(name="First Aid", expiry=TODAY + timedelta(days=200), employee="E-1", refresher="BLS Refresher", file=None):
    return run(create_new_certification(
        employee_id=employee, employee_name="Tariro Moyo", department="Engineering", certification_name=name,
        expiry_date=expiry, required_refresher=refresher, certificate_file=file, current_user=USER,
    ))


def test_a_record_is_saved_in_the_table_and_read_back(db):
    created = add()
    assert db.tables["training_certifications"][0]["certification_name"] == "First Aid"
    listed = run(get_all_certifications())
    assert [r.id for r in listed] == [created.id]
    assert listed[0].status == "Valid"


def test_there_are_no_mock_people_when_nothing_was_entered(db):
    assert run(get_all_certifications()) == []


def test_status_is_computed_on_read_from_the_expiry_date(db):
    add(name="Expired one", expiry=TODAY - timedelta(days=1))
    add(name="Due soon", expiry=TODAY + timedelta(days=30))
    statuses = {r.certification_name: r.status for r in run(get_all_certifications())}
    assert statuses == {"Expired one": "Expired", "Due soon": "Due Soon"}


def test_an_attached_certificate_is_really_stored_and_linked(db):
    created = add(file=_Upload("cert.pdf"))
    row = db.tables["training_certifications"][0]
    assert row["certificate_path"] in db.files
    assert created.certificate_url == f"https://storage.example/{row['certificate_path']}"


def test_an_unsupported_certificate_type_is_refused(db):
    with pytest.raises(HTTPException) as err:
        add(file=_Upload("payload.exe"))
    assert err.value.status_code == 400
    assert "training_certifications" not in db.tables


def test_update_changes_only_the_fields_sent_and_replaces_the_file(db):
    created = add(file=_Upload("old.pdf"))
    old_path = db.tables["training_certifications"][0]["certificate_path"]
    updated = run(update_certification(created.id, employee_id=None, employee_name=None, department=None,
                                       certification_name="First Aid & CPR", expiry_date=None, required_refresher=None,
                                       certificate_file=_Upload("new.pdf"), current_user=USER))
    assert updated.certification_name == "First Aid & CPR"
    assert updated.employee_name == "Tariro Moyo"
    assert old_path not in db.files and len(db.files) == 1


def test_delete_removes_the_record_and_its_file(db):
    created = add(file=_Upload("cert.pdf"))
    run(delete_certification(created.id, current_user=MANAGER))
    assert db.tables["training_certifications"] == [] and db.files == {}


def test_a_missing_record_is_a_404(db):
    with pytest.raises(HTTPException) as err:
        run(get_certification("nope"))
    assert err.value.status_code == 404


def test_a_database_failure_is_reported_not_shown_as_an_empty_register(db):
    db.fail = True
    with pytest.raises(HTTPException) as err:
        run(get_all_certifications())
    assert err.value.status_code == 502


def test_reports_are_computed_from_the_stored_records(db):
    add(name="A", expiry=TODAY - timedelta(days=5), refresher="BLS Refresher")
    add(name="B", expiry=TODAY + timedelta(days=20), refresher="BLS Refresher", employee="E-2")
    add(name="C", expiry=TODAY + timedelta(days=400), refresher="Annual check", employee="E-2")
    assert run(get_compliance_rate()) == {"compliance_rate": 66.67, "total_tracked": 3, "non_compliant": 1}
    assert run(get_due_refreshers())[0] == {"refresher": "BLS Refresher", "employees_due": 1}
    assert [r.certification_name for r in run(get_employee_certifications("E-2"))] == ["B", "C"]
    expiring = run(get_expiring_certifications(days=90))
    assert expiring["count"] == 1 and expiring["certifications"][0]["days_until_expiry"] == 20
    stats = run(get_training_stats())
    assert stats["totalCertifications"] == 3 and stats["statusDistribution"]["Expired"] == 1


# --- Failure paths: nothing is lost, nothing is left behind, and the reason is reported ---

def test_a_failed_read_of_one_record_is_a_502(db):
    db.fail_ops.add("select")
    with pytest.raises(HTTPException) as err:
        run(get_certification("id-1"))
    assert err.value.status_code == 502


def test_a_failed_upload_saves_nothing(db):
    db.fail_storage.add("upload")
    with pytest.raises(HTTPException) as err:
        add(file=_Upload("cert.pdf"))
    assert err.value.status_code == 500 and "upload failed" in err.value.detail
    assert db.tables.get("training_certifications", []) == []


def test_a_missing_public_link_still_saves_the_record_and_its_file(db):
    db.fail_storage.add("get_public_url")
    created = add(file=_Upload("cert.pdf"))
    assert created.certificate_url is None and len(db.files) == 1


def test_a_failed_save_removes_the_file_it_had_just_uploaded(db):
    db.fail_ops.add("insert")
    with pytest.raises(HTTPException) as err:
        add(file=_Upload("cert.pdf"))
    assert err.value.status_code == 500 and db.files == {}


def test_a_failed_update_keeps_the_old_file_and_removes_the_new_one(db):
    created = add(file=_Upload("old.pdf"))
    old_path = db.tables["training_certifications"][0]["certificate_path"]
    db.fail_ops.add("update")
    with pytest.raises(HTTPException) as err:
        run(update_certification(created.id, employee_id=None, employee_name=None, department=None, certification_name="New",
                                 expiry_date=None, required_refresher=None, certificate_file=_Upload("new.pdf"), current_user=USER))
    assert err.value.status_code == 500 and list(db.files) == [old_path]


def test_an_update_to_a_record_deleted_meanwhile_is_a_404_not_a_false_save(db, monkeypatch):
    monkeypatch.setattr(training_mod, "_one_row", lambda _id: {"id": "gone", "expiry_date": str(TODAY), "certificate_path": None})
    with pytest.raises(HTTPException) as err:
        run(update_certification("gone", employee_id=None, employee_name=None, department=None, certification_name="X",
                                 expiry_date=None, required_refresher=None, certificate_file=_Upload("new.pdf"), current_user=USER))
    assert err.value.status_code == 404 and db.files == {}


def test_a_failed_delete_keeps_the_record_and_its_file(db):
    created = add(file=_Upload("cert.pdf"))
    db.fail_ops.add("delete")
    with pytest.raises(HTTPException) as err:
        run(delete_certification(created.id, current_user=MANAGER))
    assert err.value.status_code == 500 and len(db.tables["training_certifications"]) == 1 and len(db.files) == 1


def test_a_file_that_cannot_be_cleaned_up_does_not_fail_the_delete(db):
    created = add(file=_Upload("cert.pdf"))
    db.fail_storage.add("remove")
    assert run(delete_certification(created.id, current_user=MANAGER))["message"]
    assert db.tables["training_certifications"] == []


def test_the_compliance_rate_of_an_empty_register_is_full(db):
    assert run(get_compliance_rate()) == {"compliance_rate": 100.0, "total_tracked": 0, "non_compliant": 0}
