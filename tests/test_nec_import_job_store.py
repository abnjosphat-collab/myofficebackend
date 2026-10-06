"""Filesystem job store: lifecycle, duplicate-upload detection and error paths."""
import json
import os

import pytest

from app.nec_import import job_store


@pytest.fixture(autouse=True)
def jobs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_DIR", tmp_path / "jobs")
    return tmp_path / "jobs"


def test_create_job_writes_meta_for_the_payroll_period(jobs_dir):
    meta = job_store.create_job(2026, 9, created_by="user-1")
    assert meta["status"] == "draft"
    assert meta["created_by"] == "user-1"
    assert (meta["period_start"], meta["period_end"]) == ("2026-08-13", "2026-09-12")
    d = jobs_dir / meta["id"]
    assert (d / "documents").is_dir() and (d / "pages").is_dir()
    assert json.loads((d / "job.json").read_text())["id"] == meta["id"]
    assert job_store.load_job(meta["id"]) == meta


def test_load_job_unknown_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        job_store.load_job("nope")


def test_list_jobs_empty_when_directory_missing():
    assert job_store.list_jobs() == []


def test_list_jobs_newest_first_skips_corrupt_and_respects_limit(jobs_dir):
    first = job_store.create_job(2026, 7)
    second = job_store.create_job(2026, 8)
    third = job_store.create_job(2026, 9)
    for offset, meta in enumerate((first, second, third)):
        os.utime(jobs_dir / meta["id"], (1000 + offset, 1000 + offset))
    corrupt = jobs_dir / "corrupt"
    corrupt.mkdir()
    (corrupt / "job.json").write_text("{not json")
    (jobs_dir / "stray.txt").write_text("not a job directory")
    os.utime(corrupt, (500, 500))

    ids = [j["id"] for j in job_store.list_jobs()]
    assert ids == [third["id"], second["id"], first["id"]]
    assert [j["id"] for j in job_store.list_jobs(limit=2)] == [third["id"], second["id"]]


def test_add_document_stores_file_and_moves_draft_to_uploaded(jobs_dir):
    job = job_store.create_job(2026, 9)
    doc = job_store.add_document(job["id"], "scan 1/2 (final).pdf", b"%PDF-data")
    assert doc["size_bytes"] == 9
    assert "/" not in doc["stored_path"] and " " not in doc["stored_path"]
    assert (jobs_dir / job["id"] / "documents" / doc["stored_path"]).read_bytes() == b"%PDF-data"
    meta = job_store.load_job(job["id"])
    assert meta["status"] == "uploaded"
    assert [d["id"] for d in meta["documents"]] == [doc["id"]]


def test_add_document_same_content_is_flagged_duplicate_not_stored_twice(jobs_dir):
    job = job_store.create_job(2026, 9)
    original = job_store.add_document(job["id"], "a.pdf", b"same")
    again = job_store.add_document(job["id"], "b.pdf", b"same")
    assert again["id"] == original["id"]
    assert again["duplicate_of_upload"] is True
    assert len(list((jobs_dir / job["id"] / "documents").iterdir())) == 1


def test_add_document_keeps_a_later_status(jobs_dir):
    job = job_store.create_job(2026, 9)
    job_store.set_job_status(job["id"], "review_ready")
    job_store.add_document(job["id"], "a.pdf", b"x")
    assert job_store.load_job(job["id"])["status"] == "review_ready"


def test_review_json_round_trip_marks_review_ready():
    job = job_store.create_job(2026, 9)
    with pytest.raises(FileNotFoundError):
        job_store.load_review_json(job["id"])
    review = {"source": {"method": "manual"}, "sheets": []}
    job_store.save_review_json(job["id"], review)
    assert job_store.load_review_json(job["id"]) == review
    meta = job_store.load_job(job["id"])
    assert meta["status"] == "review_ready"
    assert meta["extraction_version"] == "manual"


def test_review_json_prefers_explicit_extraction_version():
    job = job_store.create_job(2026, 9)
    job_store.save_review_json(job["id"], {"extraction_version": "v7", "source": {"method": "m"}})
    assert job_store.load_job(job["id"])["extraction_version"] == "v7"


def test_set_job_status_records_and_clears_error():
    job = job_store.create_job(2026, 9)
    failed = job_store.set_job_status(job["id"], "failed", error="boom")
    assert (failed["status"], failed["error"]) == ("failed", "boom")
    ok = job_store.set_job_status(job["id"], "review_ready")
    assert ok["error"] is None


def test_save_apply_report_completes_job_only_when_applied(jobs_dir):
    job = job_store.create_job(2026, 9)
    job_store.set_job_status(job["id"], "review_ready")

    dry = job_store.save_apply_report(job["id"], {"apply": False})
    assert job_store.load_job(job["id"])["status"] == "review_ready"
    assert (jobs_dir / job["id"] / dry).is_file()

    real = job_store.save_apply_report(job["id"], {"apply": True})
    meta = job_store.load_job(job["id"])
    assert meta["status"] == "completed"
    assert meta["last_apply_report_path"] == real
    assert json.loads((jobs_dir / job["id"] / real).read_text()) == {"apply": True}
