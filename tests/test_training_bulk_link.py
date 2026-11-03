# tests/test_training_bulk_link.py â€” employee-linked training records and bulk entry.
#
# Pins the behavior change with the project's TableFake recipe (same as
# test_training_endpoints.py): exact register matches snapshot official values,
# history survives later personnel edits, the date-keyed duplicate guard blocks
# accidental repeats but never renewals, and bulk entry reports per-person
# outcomes with retry-safe skips. No production access; all data is synthetic.

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.routers import training as training_mod
from app.clock import today_harare
from tests._table_fake import TableFake
from app.routers.training import (
    BulkTrainingItem,
    BulkTrainingRequest,
    create_bulk_certifications,
    create_new_certification,
    get_all_certifications,
    update_certification,
)

TODAY = today_harare()
USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
SCOPED = {"user_id": "s1", "email": "s1@x.com", "role": "user",
          "scope": {"kind": "section", "value": "Mechanical"}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(monkeypatch):
    fake = TableFake()
    monkeypatch.setattr(training_mod, "supabase", fake)
    fake.tables["employees"] = [
        {"id": 1, "employee_id": "C1165", "first_name": "Rudo", "last_name": "Moyo",
         "designation": "Fitter Class 1", "section": "Mechanical", "department": "Engineering"},
        {"id": 2, "employee_id": "C1166", "first_name": "Tino", "last_name": "Sibanda",
         "designation": "Electrician", "section": "Electrical", "department": "Engineering"},
    ]
    return fake


def add_single(employee="c1165", name="Nobody Here", dept="Submitted Dept", cert="Safety Induction",
               expiry="default", refresher="", provider="", training_date=None, user=USER,
               start=None, completion=None, grade="", details="", trainers="", venue=""):
    return run(create_new_certification(
        employee_id=employee, employee_name=name, department=dept, certification_name=cert,
        expiry_date=(TODAY + timedelta(days=200)) if expiry == "default" else expiry,
        required_refresher=refresher,
        provider=provider, training_date=training_date, certificate_file=None, current_user=user,
        start_date=start, completion_date=completion, grade=grade, details=details,
        trainers=trainers, venue=venue,
    ))


def bulk(cert="Safety Induction", day=None, expiry="default", provider="TechEdge", items=None,
         evidence=None, user=USER, batch="batch-1"):
    return run(create_bulk_certifications(BulkTrainingRequest(
        certification_name=cert,
        expiry_date=(TODAY + timedelta(days=365)) if expiry == "default" else expiry,
        training_date=day or TODAY, provider=provider, required_refresher="Annual",
        group_evidence_url=evidence, batch_id=batch,
        items=items if items is not None else [BulkTrainingItem(employee_id="C1165")],
    ), current_user=user))


def test_exact_register_match_snapshots_official_values(db):
    created = add_single(employee="  c1165 ", name="Wrong Name", dept="Wrong Dept",
                         training_date=TODAY, provider="TechEdge")
    assert created.employee_id == "C1165"
    assert created.employee_name == "Rudo Moyo"
    assert created.department == "Engineering"
    assert created.snapshot_designation == "Fitter Class 1"
    assert created.snapshot_section == "Mechanical"
    assert created.snapshot_department == "Engineering"
    assert created.provider == "TechEdge"
    assert created.training_date == TODAY


def test_unmatched_id_keeps_submitted_values_as_legacy_row(db):
    created = add_single(employee=" contractor-9 ", name="A Contractor", dept="Maintenance")
    assert created.employee_id == "CONTRACTOR-9"
    assert created.employee_name == "A Contractor"
    assert created.department == "Maintenance"
    assert created.snapshot_designation == ""
    assert created.snapshot_section == ""
    assert created.snapshot_department == "Maintenance"


def test_history_survives_later_register_edits(db):
    created = add_single(employee="C1165", training_date=TODAY)
    assert created.snapshot_designation == "Fitter Class 1"
    db.tables["employees"][0]["designation"] = "Fitter Class 2"
    db.tables["employees"][0]["section"] = "Rebuilt"
    listed = run(get_all_certifications(current_user=USER))
    assert listed[0].snapshot_designation == "Fitter Class 1"
    assert listed[0].snapshot_section == "Mechanical"


def test_same_person_cert_date_is_refused_but_renewal_is_allowed(db):
    add_single(employee="C1165", cert="First Aid", training_date=TODAY)
    with pytest.raises(HTTPException) as err:
        add_single(employee="c1165", cert=" first aid ", training_date=TODAY)
    assert err.value.status_code == 409
    renewal = add_single(employee="C1165", cert="First Aid", training_date=TODAY + timedelta(days=365))
    assert renewal.training_date == TODAY + timedelta(days=365)
    other_cert = add_single(employee="C1165", cert="Fire Marshal", training_date=TODAY)
    assert other_cert.certification_name == "Fire Marshal"
    other_person = add_single(employee="C1166", cert="First Aid", training_date=TODAY)
    assert other_person.employee_id == "C1166"


def test_rows_without_a_training_date_never_block_legacy_workflows(db):
    first = add_single(employee="C1165", cert="First Aid")
    second = add_single(employee="C1165", cert="First Aid")
    assert first.id != second.id


def test_bulk_creates_each_person_with_shared_details(db):
    res = bulk(items=[BulkTrainingItem(employee_id="C1165"),
                      BulkTrainingItem(employee_id="c1166", required_refresher="Biannual")])
    assert (res.created, res.skipped, res.failed, res.total) == (2, 0, 0, 2)
    assert res.batch_id == "batch-1"
    rows = db.tables["training_certifications"]
    assert len(rows) == 2
    by_id = {r["employee_id"]: r for r in rows}
    assert by_id["C1165"]["employee_name"] == "Rudo Moyo"
    assert by_id["C1165"]["snapshot_designation"] == "Fitter Class 1"
    assert by_id["C1166"]["employee_name"] == "Tino Sibanda"
    assert by_id["C1166"]["required_refresher"] == "Biannual"
    assert by_id["C1165"]["provider"] == "TechEdge"
    assert by_id["C1165"]["training_date"] == TODAY.isoformat()
    assert by_id["C1165"]["batch_id"] == "batch-1"
    # Shared evidence is group evidence, never anyone's certificate.
    assert by_id["C1165"]["certificate_url"] is None
    assert by_id["C1165"]["group_evidence_url"] is None


def test_bulk_group_evidence_link_is_stored_apart_from_certificates(db):
    res = bulk(items=[BulkTrainingItem(employee_id="C1165")],
               evidence="https://files.example/attendance.pdf", batch="batch-ev")
    assert res.created == 1
    row = db.tables["training_certifications"][0]
    assert row["group_evidence_url"] == "https://files.example/attendance.pdf"
    assert row["certificate_url"] is None


def test_bulk_retry_skips_confirmed_rows_and_keeps_failures(db):
    first = bulk(items=[BulkTrainingItem(employee_id="C1165"),
                        BulkTrainingItem(employee_id=""),
                        BulkTrainingItem(employee_id="C1165")], batch="batch-r1")
    assert (first.created, first.skipped, first.failed) == (1, 1, 1)
    assert first.results[1].uncertain is False
    retry = bulk(items=[BulkTrainingItem(employee_id="C1165"),
                        BulkTrainingItem(employee_id="C1166")], batch="batch-r1")
    assert (retry.created, retry.skipped, retry.failed) == (1, 1, 0)
    assert len(db.tables["training_certifications"]) == 2


def test_bulk_failure_is_reported_not_shown_as_empty_success(db):
    fake_fail = db
    fake_fail.fail_ops = {"insert"}
    res = bulk(items=[BulkTrainingItem(employee_id="C1165")], user=USER)
    assert res.created == 0
    assert res.failed == 1
    assert res.results[0].uncertain is True
    assert "confirmation" in (res.results[0].reason or "")
    assert db.tables.get("training_certifications", []) == []


def test_bulk_enforces_department_isolation_per_person(db):
    res = bulk(items=[BulkTrainingItem(employee_id="C1165"),
                      BulkTrainingItem(employee_id="C1166")], user=SCOPED)
    assert (res.created, res.failed) == (1, 1)
    assert res.results[0].outcome == "created"
    assert res.results[1].outcome == "failed"
    assert "visible" in (res.results[1].reason or "")
    assert res.results[1].uncertain is False
    assert len(db.tables["training_certifications"]) == 1


def test_scoped_create_outside_scope_is_refused(db):
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1166", training_date=TODAY, user=SCOPED)
    assert err.value.status_code == 404


def test_repointing_onto_the_register_refreshes_snapshots(db):
    created = add_single(employee="contractor-9", name="A Contractor", dept="Maintenance")
    assert created.snapshot_designation == ""
    moved = run(update_certification(
        str(created.id), employee_id="C1165", employee_name=None, department=None,
        certification_name=None, expiry_date=None, required_refresher=None,
        provider=None, training_date=None, certificate_file=None, current_user=USER))
    assert moved.employee_name == "Rudo Moyo"
    assert moved.snapshot_designation == "Fitter Class 1"
    assert moved.snapshot_section == "Mechanical"


def test_training_without_expiry_never_lapses(db):
    created = add_single(employee="C1165", cert="Induction", training_date=TODAY, expiry=None)
    assert created.expiry_date is None
    assert created.status == "Valid"
    listed = run(get_all_certifications(current_user=USER))
    assert listed[0].expiry_date is None
    assert listed[0].status == "Valid"


def test_expiry_can_be_cleared_on_update(db):
    created = add_single(employee="C1165", cert="Induction", training_date=TODAY)
    assert created.expiry_date is not None
    cleared = run(update_certification(
        str(created.id), employee_id=None, employee_name=None, department=None,
        certification_name=None, expiry_date=None, required_refresher=None,
        provider=None, training_date=None, no_expiry=True,
        certificate_file=None, current_user=USER))
    assert cleared.expiry_date is None
    assert cleared.status == "Valid"


def test_bulk_without_expiry_and_duplicate_guard_still_applies(db):
    res = bulk(cert="Induction", day=TODAY, expiry=None,
               items=[BulkTrainingItem(employee_id="C1165")])
    assert (res.created, res.failed) == (1, 0)
    again = bulk(cert="Induction", day=TODAY, expiry=None,
                 items=[BulkTrainingItem(employee_id="C1165")])
    assert (again.created, again.skipped) == (0, 1)
    renewal = bulk(cert="Induction", day=TODAY + timedelta(days=30), expiry=None,
                   items=[BulkTrainingItem(employee_id="C1165")])
    assert renewal.created == 1


def test_free_text_edits_cannot_overwrite_linked_register_values(db):
    created = add_single(employee="C1165", training_date=TODAY)
    edited = run(update_certification(
        str(created.id), employee_id=None, employee_name="Invented Name", department="Invented Dept",
        certification_name=None, expiry_date=None, required_refresher=None,
        provider=None, training_date=None, certificate_file=None, current_user=USER))
    assert edited.employee_name == "Rudo Moyo"
    assert edited.department == "Engineering"
    assert edited.snapshot_designation == "Fitter Class 1"


def test_full_details_round_trip(db):
    created = add_single(employee="C1165", cert="Winder Driving", training_date=TODAY,
                         start=TODAY - timedelta(days=5), completion=TODAY, grade="Merit",
                         details="Rope inspection, brake tests", trainers="T. Moyo, J. Sithole",
                         venue="Tech Edge Centre")
    assert created.start_date == TODAY - timedelta(days=5)
    assert created.completion_date == TODAY
    assert created.grade == "Merit"
    assert created.details == "Rope inspection, brake tests"
    assert created.trainers == "T. Moyo, J. Sithole"
    assert created.venue == "Tech Edge Centre"
    listed = run(get_all_certifications(current_user=USER))
    assert listed[0].completion_date == TODAY
    assert listed[0].grade == "Merit"


def test_unknown_grade_is_refused(db):
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1165", cert="Winder Driving", training_date=TODAY, grade="First Class")
    assert err.value.status_code == 422


def test_start_after_completion_is_refused(db):
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1165", cert="Winder Driving", training_date=TODAY,
                   start=TODAY, completion=TODAY - timedelta(days=1))
    assert err.value.status_code == 422
    res = bulk(cert="Winder Driving", day=TODAY,
               items=[BulkTrainingItem(employee_id="C1165")])
    assert res.created == 1


def test_duplicate_guard_keys_on_completion_date(db):
    add_single(employee="C1165", cert="Winder Driving", training_date=TODAY,
               completion=TODAY)
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1165", cert="Winder Driving",
                   training_date=TODAY + timedelta(days=10), completion=TODAY)
    assert err.value.status_code == 409
    renewal = add_single(employee="C1165", cert="Winder Driving",
                         training_date=TODAY + timedelta(days=10),
                         completion=TODAY + timedelta(days=10))
    assert renewal.completion_date == TODAY + timedelta(days=10)


def test_legacy_training_date_still_guards_without_completion(db):
    add_single(employee="C1165", cert="Winder Driving", training_date=TODAY)
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1165", cert="Winder Driving", training_date=TODAY)
    assert err.value.status_code == 409


def test_whitespace_variant_cert_is_a_duplicate(db):
    add_single(employee="C1165", cert="Winder Driving", training_date=TODAY)
    with pytest.raises(HTTPException) as err:
        add_single(employee="C1165", cert="  Winder   Driving ", training_date=TODAY)
    assert err.value.status_code == 409

