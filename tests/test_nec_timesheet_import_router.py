"""HTTP-level tests for the NEC scan import jobs API (auth, upload validation, review,
extract, preview and apply error paths). Jobs live in a tmp dir; no database is touched."""
import json

import pytest
from fastapi.testclient import TestClient

import app.auth as auth
import app.routers.nec_timesheet_import as nec
from app.nec_import import job_store
from main import app

BASE = "/api/nec-timesheet-import"
ORIGINAL_GET_CURRENT_USER = auth.get_current_user


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(nec, "SNAPSHOT_DIR", tmp_path / "snapshots")

    async def manager(authorization=None):
        return {"user_id": "u1", "email": "boss@example.com", "role": "manager"}

    monkeypatch.setattr(auth, "get_current_user", manager)
    app.dependency_overrides[ORIGINAL_GET_CURRENT_USER] = manager
    yield TestClient(app)
    app.dependency_overrides.pop(ORIGINAL_GET_CURRENT_USER, None)


def _job(client, year=2026, month=9):
    r = client.post(f"{BASE}/jobs", json={"payroll_year": year, "payroll_month": month})
    assert r.status_code == 200, r.text
    return r.json()


def _review(start="2026-08-13", end="2026-09-12"):
    return {"period": {"start_date": start, "end_date": end}, "sheets": []}


def _upload_review(client, job_id, payload, name="review.json"):
    return client.post(
        f"{BASE}/jobs/{job_id}/review-json",
        files={"file": (name, json.dumps(payload).encode(), "application/json")},
    )


def test_create_job_validates_payroll_month_and_records_creator(client):
    assert client.post(f"{BASE}/jobs", json={"payroll_year": 2026, "payroll_month": 13}).status_code == 422
    job = _job(client)
    assert job["created_by"] == "boss@example.com"
    assert (job["period_start"], job["period_end"]) == ("2026-08-13", "2026-09-12")


def test_list_and_get_job(client):
    job = _job(client)
    listed = client.get(f"{BASE}/jobs").json()["jobs"]
    assert [j["id"] for j in listed] == [job["id"]]
    assert client.get(f"{BASE}/jobs/{job['id']}").json()["id"] == job["id"]
    assert client.get(f"{BASE}/jobs/missing").status_code == 404
    assert client.get(f"{BASE}/jobs?limit=0").status_code == 422


def test_cancel_job_and_unknown_job(client):
    job = _job(client)
    assert client.post(f"{BASE}/jobs/{job['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"{BASE}/jobs/missing/cancel").status_code == 404


def test_writes_require_manager_role(client, monkeypatch):
    async def viewer(authorization=None):
        return {"user_id": "u2", "email": "v@example.com", "role": "viewer"}

    monkeypatch.setattr(auth, "get_current_user", viewer)
    r = client.post(f"{BASE}/jobs", json={"payroll_year": 2026, "payroll_month": 9})
    assert r.status_code == 403
    assert client.get(f"{BASE}/jobs").status_code == 200


def test_upload_pdf_accepts_pdf_once_and_rejects_other_types(client):
    job = _job(client)
    url = f"{BASE}/jobs/{job['id']}/documents"
    bad = client.post(url, files={"file": ("scan.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and "Unsupported file type" in bad.json()["detail"]
    assert client.post(f"{BASE}/jobs/missing/documents",
                       files={"file": ("a.pdf", b"x", "application/pdf")}).status_code == 404

    first = client.post(url, files={"file": ("scan.pdf", b"%PDF-1", "application/pdf")}).json()
    again = client.post(url, files={"file": ("copy.pdf", b"%PDF-1", "application/pdf")}).json()
    assert again["id"] == first["id"] and again["duplicate_of_upload"] is True


def test_upload_review_json_happy_path_and_period_mismatch(client):
    job = _job(client)
    ok = _upload_review(client, job["id"], {**_review(), "schema_version": 3})
    assert ok.status_code == 200 and ok.json() == {"ok": True, "schema_version": 3}
    assert client.get(f"{BASE}/jobs/{job['id']}").json()["status"] == "review_ready"

    start = _upload_review(client, job["id"], _review(start="2026-07-13"))
    assert start.status_code == 400 and "start" in start.json()["detail"]
    end = _upload_review(client, job["id"], _review(end="2026-10-12"))
    assert end.status_code == 400 and "end" in end.json()["detail"]


def test_upload_review_json_rejects_bad_input(client):
    job = _job(client)
    assert _upload_review(client, "missing", _review()).status_code == 404
    garbage = client.post(f"{BASE}/jobs/{job['id']}/review-json",
                          files={"file": ("r.json", b"{nope", "application/json")})
    assert garbage.status_code == 400 and garbage.json()["detail"] == "Invalid JSON"
    invalid = _upload_review(client, job["id"], {"period": {}, "sheets": "x"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["message"] == "Review JSON validation failed"
    assert client.post(f"{BASE}/jobs/{job['id']}/review-json",
                       files={"file": ("r.txt", b"{}", "text/plain")}).status_code == 400


def test_extract_with_default_provider_fails_with_502_and_keeps_job_usable(client, monkeypatch):
    monkeypatch.delenv("NEC_IMPORT_EXTRACTION_PROVIDER", raising=False)
    job = _job(client)
    r = client.post(f"{BASE}/jobs/{job['id']}/extract")
    assert r.status_code == 502 and "not configured" in r.json()["detail"]
    meta = client.get(f"{BASE}/jobs/{job['id']}").json()
    assert meta["status"] == "uploaded" and "not configured" in meta["error"]
    assert client.post(f"{BASE}/jobs/missing/extract").status_code == 404


def test_extract_with_uploaded_review_marks_provider(client, monkeypatch):
    monkeypatch.setenv("NEC_IMPORT_EXTRACTION_PROVIDER", "uploaded")
    job = _job(client)
    _upload_review(client, job["id"], _review())
    r = client.post(f"{BASE}/jobs/{job['id']}/extract")
    assert r.json() == {"status": "review_ready", "provider": "review_json_upload"}
    assert client.get(f"{BASE}/jobs/{job['id']}").json()["extraction_provider"] == "review_json_upload"


def test_config_reports_provider(client, monkeypatch):
    monkeypatch.delenv("NEC_IMPORT_EXTRACTION_PROVIDER", raising=False)
    cfg = client.get(f"{BASE}/config").json()
    assert cfg["extraction_provider"] == "manual_review_json"
    assert cfg["requires_review_json_upload"] is True


def test_preview_needs_a_review_and_records_last_preview(client, monkeypatch):
    job = _job(client)
    assert client.get(f"{BASE}/jobs/{job['id']}/preview").status_code == 404
    _upload_review(client, job["id"], _review())
    monkeypatch.setattr(nec, "build_preview", lambda review, s, e: {"period": f"{s}..{e}", "sheets": []})
    body = client.get(f"{BASE}/jobs/{job['id']}/preview").json()
    assert body["preview"]["period"] == "2026-08-13..2026-09-12"
    assert body["job"]["last_preview_at"] == "2026-08-13..2026-09-12"


def test_apply_defaults_to_dry_run_and_only_real_apply_completes_job(client, monkeypatch):
    job = _job(client)
    assert client.post(f"{BASE}/jobs/{job['id']}/apply").status_code == 404
    _upload_review(client, job["id"], _review())
    calls = []

    def fake_run(review, *, period_start, period_end, apply, snapshot_dir):
        calls.append(apply)
        return {"stats": {"patched": 2}, "apply": apply}

    monkeypatch.setattr(nec, "run_import_from_review", fake_run)

    dry = client.post(f"{BASE}/jobs/{job['id']}/apply").json()
    assert dry["dry_run"] is True and dry["stats"] == {"patched": 2}
    assert client.get(f"{BASE}/jobs/{job['id']}").json()["status"] == "review_ready"

    real = client.post(f"{BASE}/jobs/{job['id']}/apply?dry_run=false").json()
    assert real["dry_run"] is False and calls == [False, True]
    assert client.get(f"{BASE}/jobs/{job['id']}").json()["status"] == "completed"


def test_apply_refuses_cancelled_job(client):
    job = _job(client)
    _upload_review(client, job["id"], _review())
    client.post(f"{BASE}/jobs/{job['id']}/cancel")
    r = client.post(f"{BASE}/jobs/{job['id']}/apply?dry_run=false")
    assert r.status_code == 400 and r.json()["detail"] == "Job cancelled"


def test_pdf_page_preview_paths(client, tmp_path, monkeypatch):
    job = _job(client)
    doc = client.post(f"{BASE}/jobs/{job['id']}/documents",
                      files={"file": ("scan.pdf", b"%PDF-1", "application/pdf")}).json()
    url = f"{BASE}/jobs/{job['id']}/documents/{doc['id']}/pages/0/preview"

    assert client.get(f"{BASE}/jobs/{job['id']}/documents/nope/pages/0/preview").status_code == 404

    monkeypatch.setattr(nec, "render_page_png", lambda path, idx: None)
    assert client.get(url).status_code == 503

    monkeypatch.setattr(nec, "render_page_png", lambda path, idx: "QUJD")
    assert client.get(url).json() == {"page_index": 0, "image_base64_png": "QUJD"}

    (job_store.job_dir(job["id"]) / "documents" / doc["stored_path"]).unlink()
    assert client.get(url).status_code == 404
