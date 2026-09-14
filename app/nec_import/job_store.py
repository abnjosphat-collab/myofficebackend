"""Filesystem-backed NEC import jobs (gitignored under data/nec_import_jobs/)."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
JOBS_DIR = ROOT / "data" / "nec_import_jobs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def create_job(payroll_year: int, payroll_month: int, created_by: Optional[str] = None) -> dict:
    from app.nec_import.period import nec_period_for_payroll_month

    start, end = nec_period_for_payroll_month(payroll_year, payroll_month)
    job_id = uuid.uuid4().hex
    d = job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "documents").mkdir(exist_ok=True)
    (d / "pages").mkdir(exist_ok=True)

    meta = {
        "id": job_id,
        "status": "draft",
        "payroll_year": payroll_year,
        "payroll_month": payroll_month,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": created_by,
        "documents": [],
        "review_json_path": None,
        "extraction_provider": None,
        "extraction_version": None,
        "last_preview_at": None,
        "last_apply_report_path": None,
        "error": None,
    }
    _write_meta(job_id, meta)
    return meta


def _write_meta(job_id: str, meta: dict) -> None:
    meta["updated_at"] = _now()
    path = job_dir(job_id) / "job.json"
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_jobs(limit: int = 50) -> List[dict]:
    if not JOBS_DIR.is_dir():
        return []
    jobs = []
    for p in sorted(JOBS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_dir():
            continue
        try:
            jobs.append(load_job(p.name))
        except Exception:
            continue
        if len(jobs) >= limit:
            break
    return jobs


def add_document(job_id: str, filename: str, content: bytes) -> dict:
    meta = load_job(job_id)
    sha = hashlib.sha256(content).hexdigest()
    for doc in meta.get("documents", []):
        if doc.get("sha256") == sha:
            doc["duplicate_of_upload"] = True
            return doc
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)[:120]
    doc_id = uuid.uuid4().hex[:12]
    out = job_dir(job_id) / "documents" / f"{doc_id}_{safe_name}"
    out.write_bytes(content)
    doc = {
        "id": doc_id,
        "filename": filename,
        "stored_path": out.name,
        "sha256": sha,
        "size_bytes": len(content),
        "uploaded_at": _now(),
    }
    meta.setdefault("documents", []).append(doc)
    if meta["status"] == "draft":
        meta["status"] = "uploaded"
    _write_meta(job_id, meta)
    return doc


def save_review_json(job_id: str, review: dict) -> None:
    meta = load_job(job_id)
    path = job_dir(job_id) / "review.json"
    path.write_text(json.dumps(review, indent=2), encoding="utf-8")
    meta["review_json_path"] = "review.json"
    meta["status"] = "review_ready"
    meta["extraction_version"] = review.get("extraction_version") or (review.get("source") or {}).get("method")
    _write_meta(job_id, meta)


def load_review_json(job_id: str) -> dict:
    meta = load_job(job_id)
    rel = meta.get("review_json_path")
    if not rel:
        raise FileNotFoundError("no review json")
    return json.loads((job_dir(job_id) / rel).read_text(encoding="utf-8"))


def set_job_status(job_id: str, status: str, error: Optional[str] = None) -> dict:
    meta = load_job(job_id)
    meta["status"] = status
    meta["error"] = error
    _write_meta(job_id, meta)
    return meta


def save_apply_report(job_id: str, report: dict) -> str:
    meta = load_job(job_id)
    name = f"apply_report_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path = job_dir(job_id) / name
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    meta["last_apply_report_path"] = name
    meta["status"] = "completed" if report.get("apply") else meta.get("status", "review_ready")
    _write_meta(job_id, meta)
    return name
