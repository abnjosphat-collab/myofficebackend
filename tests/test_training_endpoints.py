# tests/test_training_endpoints.py — the route handlers behind training.py's
# certification/compliance API. training.py was 44% covered with only check_status and
# find_record tested (see test_training_status.py, read first). Unlike every other
# router in this codebase, training.py has NO Supabase backing at all — it's a real
# in-memory mock list, CERTIFICATIONS_DB, module-level and mutated in place by several
# endpoints (create/update/delete). Every test below runs through an autouse fixture
# that snapshots CERTIFICATIONS_DB before the test and restores both its CONTENTS and
# its object IDENTITY afterward — identity matters because delete_certification
# reassigns the module global wholesale (`global CERTIFICATIONS_DB; CERTIFICATIONS_DB =
# [...]`) rather than mutating in place, and test_training_status.py holds its own
# `from ... import CERTIFICATIONS_DB` reference captured at collection time that must
# keep pointing at a list with equivalent, correctly-`is`-comparable contents.
#
# asyncio.sleep is stubbed out for these tests (a few endpoints simulate upload latency
# with a real sleep) — that's a test-speed optimization, not a change to app behavior.

import copy
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.routers import training as training_mod
from app.routers.training import (
    CertificateRecord, check_status,
    get_all_certifications, create_new_certification, get_certification, update_certification,
    delete_certification, get_compliance_rate, get_due_refreshers, get_employee_certifications,
    get_expiring_certifications, get_training_stats,
)


@pytest.fixture(autouse=True)
def _reset_certifications_db(monkeypatch):
    original_list = training_mod.CERTIFICATIONS_DB
    snapshot = [rec.model_copy(deep=True) for rec in original_list]
    # Stub asyncio.sleep so the "simulate network/upload latency" calls don't actually
    # slow the test suite down.
    async def _no_sleep(*a, **k):
        return None
    monkeypatch.setattr(training_mod.asyncio, "sleep", _no_sleep)
    yield
    training_mod.CERTIFICATIONS_DB = original_list
    original_list[:] = snapshot


def _set_db(records):
    training_mod.CERTIFICATIONS_DB[:] = records


class _FakeUpload:
    def __init__(self, filename):
        self.filename = filename


CURRENT_USER = {"user_id": "u1", "email": "u1@x.com", "role": "user"}
MANAGER_USER = {"user_id": "m1", "email": "m1@x.com", "role": "manager"}

TODAY = date.today()


def _cert(**kw):
    base = dict(
        employee_id="E100", employee_name="Test Employee", department="Testing",
        certification_name="Test Cert", expiry_date=TODAY + timedelta(days=365),
        required_refresher="Annual", certificate_url=None,
    )
    base.update(kw)
    return CertificateRecord(**base).update_status()


# ─── get_all_certifications ────────────────────────────────────────────────────────

async def test_get_all_certifications_returns_every_record_with_fresh_status():
    _set_db([
        _cert(employee_id="E1", expiry_date=TODAY - timedelta(days=5)),
        _cert(employee_id="E2", expiry_date=TODAY + timedelta(days=400)),
    ])
    result = await get_all_certifications()
    assert len(result) == 2
    statuses = {r.employee_id: r.status for r in result}
    assert statuses == {"E1": "Expired", "E2": "Valid"}


# ─── create_new_certification ───────────────────────────────────────────────────────

async def test_create_new_certification_appends_and_computes_status():
    _set_db([])
    result = await create_new_certification(
        employee_id="E200", employee_name="New Hire", department="Mining",
        certification_name="Blasting Permit", expiry_date=TODAY - timedelta(days=1),
        required_refresher="Bi-Annual", certificate_file=None, current_user=CURRENT_USER,
    )
    assert result.status == "Expired"
    assert len(training_mod.CERTIFICATIONS_DB) == 1
    assert training_mod.CERTIFICATIONS_DB[0].employee_id == "E200"


async def test_create_new_certification_valid_expiry_status():
    _set_db([])
    result = await create_new_certification(
        employee_id="E201", employee_name="New Hire 2", department="Mining",
        certification_name="HEO", expiry_date=TODAY + timedelta(days=400),
        required_refresher="Annual", certificate_file=None, current_user=CURRENT_USER,
    )
    assert result.status == "Valid"


async def test_create_new_certification_with_file_sets_a_storage_url():
    _set_db([])
    result = await create_new_certification(
        employee_id="E202", employee_name="Filer", department="Safety",
        certification_name="First Aid", expiry_date=TODAY + timedelta(days=10),
        required_refresher="Annual", certificate_file=_FakeUpload("cert.pdf"),
        current_user=CURRENT_USER,
    )
    assert result.certificate_url.startswith("/storage/certs/")
    assert result.certificate_url.endswith(".pdf")


async def test_create_new_certification_file_without_extension_defaults_to_pdf():
    _set_db([])
    result = await create_new_certification(
        employee_id="E203", employee_name="Filer2", department="Safety",
        certification_name="First Aid", expiry_date=TODAY + timedelta(days=10),
        required_refresher="Annual", certificate_file=_FakeUpload("noext"),
        current_user=CURRENT_USER,
    )
    assert result.certificate_url.endswith(".pdf")


async def test_create_new_certification_no_file_leaves_url_none():
    _set_db([])
    result = await create_new_certification(
        employee_id="E204", employee_name="NoFile", department="Safety",
        certification_name="First Aid", expiry_date=TODAY + timedelta(days=10),
        required_refresher="Annual", certificate_file=None, current_user=CURRENT_USER,
    )
    assert result.certificate_url is None


# ─── get_certification ───────────────────────────────────────────────────────────────

async def test_get_certification_found_recomputes_status():
    rec = _cert(expiry_date=TODAY + timedelta(days=1))
    _set_db([rec])
    result = await get_certification(rec.id)
    assert result.id == rec.id
    assert result.status == "Due Soon"


async def test_get_certification_missing_is_404():
    _set_db([])
    with pytest.raises(HTTPException) as exc:
        await get_certification("nonexistent")
    assert exc.value.status_code == 404


# ─── update_certification ────────────────────────────────────────────────────────────

async def test_update_certification_updates_only_provided_fields():
    rec = _cert(employee_name="Original Name", department="Old Dept")
    _set_db([rec])

    result = await update_certification(
        rec.id,
        employee_id=None, employee_name="Updated Name", department=None,
        certification_name=None, expiry_date=None, required_refresher=None,
        certificate_file=None, current_user=CURRENT_USER,
    )

    assert result.employee_name == "Updated Name"
    assert result.department == "Old Dept"  # untouched


async def test_update_certification_updates_every_settable_field_when_all_provided():
    rec = _cert(
        employee_id="E-OLD", employee_name="Old Name", department="Old Dept",
        certification_name="Old Cert", required_refresher="Old Refresher",
    )
    _set_db([rec])

    result = await update_certification(
        rec.id,
        employee_id="E-NEW", employee_name="New Name", department="New Dept",
        certification_name="New Cert", expiry_date=None, required_refresher="New Refresher",
        certificate_file=None, current_user=CURRENT_USER,
    )

    assert result.employee_id == "E-NEW"
    assert result.employee_name == "New Name"
    assert result.department == "New Dept"
    assert result.certification_name == "New Cert"
    assert result.required_refresher == "New Refresher"


async def test_update_certification_expiry_date_recomputes_status():
    rec = _cert(expiry_date=TODAY + timedelta(days=400))
    assert rec.status == "Valid"
    _set_db([rec])

    result = await update_certification(
        rec.id,
        employee_id=None, employee_name=None, department=None, certification_name=None,
        expiry_date=TODAY - timedelta(days=1), required_refresher=None,
        certificate_file=None, current_user=CURRENT_USER,
    )

    assert result.status == "Expired"


async def test_update_certification_with_new_file_overwrites_url():
    rec = _cert(certificate_url="/docs/old.pdf")
    _set_db([rec])

    result = await update_certification(
        rec.id,
        employee_id=None, employee_name=None, department=None, certification_name=None,
        expiry_date=None, required_refresher=None,
        certificate_file=_FakeUpload("new.docx"), current_user=CURRENT_USER,
    )

    assert result.certificate_url != "/docs/old.pdf"
    assert result.certificate_url.endswith(".docx")


async def test_update_certification_missing_is_404():
    _set_db([])
    with pytest.raises(HTTPException) as exc:
        await update_certification(
            "nonexistent",
            employee_id=None, employee_name=None, department=None, certification_name=None,
            expiry_date=None, required_refresher=None, certificate_file=None,
            current_user=CURRENT_USER,
        )
    assert exc.value.status_code == 404


# ─── delete_certification ────────────────────────────────────────────────────────────

async def test_delete_certification_removes_the_record():
    rec = _cert()
    _set_db([rec, _cert(employee_id="OTHER")])

    result = await delete_certification(rec.id, current_user=MANAGER_USER)

    assert result == {"message": "Certification record deleted successfully"}
    assert len(training_mod.CERTIFICATIONS_DB) == 1
    assert training_mod.CERTIFICATIONS_DB[0].employee_id == "OTHER"


async def test_delete_certification_missing_is_404():
    _set_db([_cert()])
    with pytest.raises(HTTPException) as exc:
        await delete_certification("nonexistent", current_user=MANAGER_USER)
    assert exc.value.status_code == 404


# ─── get_compliance_rate ─────────────────────────────────────────────────────────────

async def test_get_compliance_rate_computes_percentage():
    _set_db([
        _cert(employee_id="E1", expiry_date=TODAY - timedelta(days=10)),  # Expired
        _cert(employee_id="E2", expiry_date=TODAY + timedelta(days=30)),  # Due Soon
        _cert(employee_id="E3", expiry_date=TODAY + timedelta(days=200)),  # Valid
        _cert(employee_id="E4", expiry_date=TODAY + timedelta(days=400)),  # Valid
        _cert(employee_id="E5", expiry_date=TODAY - timedelta(days=1)),   # Expired
    ])
    result = await get_compliance_rate()
    assert result == {"compliance_rate": 60.0, "total_tracked": 5, "non_compliant": 2}


async def test_get_compliance_rate_empty_db_is_100_percent():
    _set_db([])
    result = await get_compliance_rate()
    assert result == {"compliance_rate": 100.0, "total_tracked": 0, "non_compliant": 0}


async def test_get_compliance_rate_all_expired_is_zero():
    _set_db([_cert(expiry_date=TODAY - timedelta(days=1))])
    result = await get_compliance_rate()
    assert result["compliance_rate"] == 0.0


# ─── get_due_refreshers ──────────────────────────────────────────────────────────────

async def test_get_due_refreshers_counts_and_ranks_top_3():
    _set_db([
        _cert(employee_id="E1", required_refresher="BLS Refresher", expiry_date=TODAY + timedelta(days=30)),
        _cert(employee_id="E2", required_refresher="BLS Refresher", expiry_date=TODAY + timedelta(days=45)),
        _cert(employee_id="E3", required_refresher="Annual HEO Check", expiry_date=TODAY + timedelta(days=200)),
        _cert(employee_id="E4", required_refresher="Annual HEO Check", expiry_date=TODAY + timedelta(days=10)),
        _cert(employee_id="E5", required_refresher="Annual HEO Check", expiry_date=TODAY + timedelta(days=20)),
        _cert(employee_id="E6", required_refresher="Gas Monitor Refresher", expiry_date=TODAY + timedelta(days=5)),
    ])
    result = await get_due_refreshers()
    assert result[0] == {"refresher": "Annual HEO Check", "employees_due": 3}
    assert result[1] == {"refresher": "BLS Refresher", "employees_due": 2}
    assert len(result) == 3  # top 3 only, Gas Monitor Refresher (count 1) dropped


async def test_get_due_refreshers_excludes_expired_records():
    _set_db([
        _cert(employee_id="E1", required_refresher="BLS Refresher", expiry_date=TODAY - timedelta(days=5)),  # expired
        _cert(employee_id="E2", required_refresher="BLS Refresher", expiry_date=TODAY + timedelta(days=30)),
    ])
    result = await get_due_refreshers()
    assert result == [{"refresher": "BLS Refresher", "employees_due": 1}]


async def test_get_due_refreshers_excludes_blank_refresher_values():
    _set_db([_cert(required_refresher="", expiry_date=TODAY + timedelta(days=30))])
    result = await get_due_refreshers()
    assert result == []


# ─── get_employee_certifications ─────────────────────────────────────────────────────

async def test_get_employee_certifications_filters_by_employee_id():
    _set_db([
        _cert(employee_id="E1", certification_name="Cert A"),
        _cert(employee_id="E1", certification_name="Cert B"),
        _cert(employee_id="E2", certification_name="Cert C"),
    ])
    result = await get_employee_certifications("E1")
    assert {r.certification_name for r in result} == {"Cert A", "Cert B"}


async def test_get_employee_certifications_no_match_is_empty_list():
    _set_db([_cert(employee_id="E1")])
    result = await get_employee_certifications("NOBODY")
    assert result == []


# ─── get_expiring_certifications ─────────────────────────────────────────────────────

async def test_get_expiring_certifications_default_90_day_window_sorted_ascending():
    _set_db([
        _cert(employee_id="E1", expiry_date=TODAY + timedelta(days=45)),
        _cert(employee_id="E2", expiry_date=TODAY + timedelta(days=10)),
        _cert(employee_id="E3", expiry_date=TODAY + timedelta(days=200)),  # outside window
        _cert(employee_id="E4", expiry_date=TODAY - timedelta(days=5)),   # already expired, excluded
    ])
    result = await get_expiring_certifications(days=90)
    assert result["days_threshold"] == 90
    assert result["count"] == 2
    ids_in_order = [c["employee_id"] for c in result["certifications"]]
    assert ids_in_order == ["E2", "E1"]
    assert result["certifications"][0]["days_until_expiry"] == 10


async def test_get_expiring_certifications_custom_window():
    _set_db([_cert(expiry_date=TODAY + timedelta(days=150))])
    result = await get_expiring_certifications(days=200)
    assert result["count"] == 1


async def test_get_expiring_certifications_boundary_today_is_included():
    _set_db([_cert(expiry_date=TODAY)])
    result = await get_expiring_certifications(days=90)
    assert result["count"] == 1
    assert result["certifications"][0]["days_until_expiry"] == 0


# ─── get_training_stats ──────────────────────────────────────────────────────────────

async def test_get_training_stats_aggregates_correctly():
    _set_db([
        _cert(employee_id="E1", department="Safety", expiry_date=TODAY - timedelta(days=5)),   # Expired
        _cert(employee_id="E2", department="Safety", expiry_date=TODAY + timedelta(days=30)),  # Due Soon
        _cert(employee_id="E3", department="Mining", expiry_date=TODAY + timedelta(days=400)),  # Valid
    ])
    result = await get_training_stats()
    assert result["totalCertifications"] == 3
    assert result["statusDistribution"] == {"Expired": 1, "Due Soon": 1, "Valid": 1}
    assert result["departmentDistribution"] == {"Safety": 2, "Mining": 1}
    assert result["complianceRate"] == round((3 - 1) / 3 * 100, 2)


async def test_get_training_stats_empty_db_compliance_rate_is_100():
    _set_db([])
    result = await get_training_stats()
    assert result["totalCertifications"] == 0
    assert result["complianceRate"] == 100.0
