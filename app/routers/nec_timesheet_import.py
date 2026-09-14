"""NEC scanned timesheet import — jobs, review, apply."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.auth import get_current_user, require_role
from app.nec_import import job_store
from app.nec_import.apply_runner import run_import_from_review
from app.nec_import.extraction.registry import get_extraction_provider
from app.nec_import.pdf_render import render_page_png
from app.nec_import.preview import build_preview
from app.nec_import.review_schema import validate_review_payload
from app.uploads import read_and_validate_upload

logger = logging.getLogger(__name__)
router = APIRouter()

PDF_MAX = 80 * 1024 * 1024
REVIEW_MAX = 40 * 1024 * 1024
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "nec_import_snapshots"


class CreateJobBody(BaseModel):
    payroll_year: int = Field(..., ge=2020, le=2100)
    payroll_month: int = Field(..., ge=1, le=12)


class SheetDispositionBody(BaseModel):
    sheet_id: str
    disposition: str = Field(..., pattern="^(selected|superseded|rejected)$")


class UpdateReviewBody(BaseModel):
    sheet_dispositions: Optional[List[SheetDispositionBody]] = None


@router.get("/jobs", dependencies=[Depends(get_current_user)])
async def list_import_jobs(limit: int = Query(50, ge=1, le=200)):
    return {"jobs": job_store.list_jobs(limit)}


@router.post("/jobs", dependencies=[Depends(require_role("manager"))])
async def create_import_job(body: CreateJobBody, current_user: dict = Depends(get_current_user)):
    meta = job_store.create_job(
        body.payroll_year,
        body.payroll_month,
        created_by=current_user.get("email") or current_user.get("sub"),
    )
    return meta


@router.get("/jobs/{job_id}", dependencies=[Depends(get_current_user)])
async def get_import_job(job_id: str):
    try:
        return job_store.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Import job not found")


@router.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_role("manager"))])
async def cancel_job(job_id: str):
    try:
        return job_store.set_job_status(job_id, "cancelled")
    except FileNotFoundError:
        raise HTTPException(404, "Import job not found")


@router.post("/jobs/{job_id}/documents", dependencies=[Depends(require_role("manager"))])
async def upload_pdf(job_id: str, file: UploadFile = File(...)):
    try:
        job_store.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Import job not found")
    content = await read_and_validate_upload(
        file, max_bytes=PDF_MAX, allowed_exts={"pdf"},
    )
    doc = job_store.add_document(job_id, file.filename or "scan.pdf", content)
    return doc


@router.get("/jobs/{job_id}/documents/{doc_id}/pages/{page_index}/preview", dependencies=[Depends(get_current_user)])
async def preview_pdf_page(job_id: str, doc_id: str, page_index: int):
    meta = job_store.load_job(job_id)
    doc = next((d for d in meta.get("documents", []) if d["id"] == doc_id), None)
    if not doc:
        raise HTTPException(404, "Document not found")
    pdf_path = job_store.job_dir(job_id) / "documents" / doc["stored_path"]
    if not pdf_path.is_file():
        raise HTTPException(404, "File missing on server")
    b64 = render_page_png(pdf_path, page_index)
    if b64 is None:
        raise HTTPException(503, "PDF preview unavailable (PyMuPDF required)")
    return {"page_index": page_index, "image_base64_png": b64}


@router.post("/jobs/{job_id}/review-json", dependencies=[Depends(require_role("manager"))])
async def upload_review_json(job_id: str, file: UploadFile = File(...)):
    try:
        meta = job_store.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Import job not found")
    raw = await read_and_validate_upload(
        file, max_bytes=REVIEW_MAX, allowed_exts={"json"},
    )
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    ok, errs = validate_review_payload(data)
    if not ok:
        raise HTTPException(400, detail={"message": "Review JSON validation failed", "errors": errs})
    # Period must match job unless explicitly flagged in JSON
    p = data.get("period") or {}
    if p.get("start_date") and p.get("start_date") != meta["period_start"]:
        raise HTTPException(400, f"Review period start {p.get('start_date')} does not match job {meta['period_start']}")
    if p.get("end_date") and p.get("end_date") != meta["period_end"]:
        raise HTTPException(400, f"Review period end {p.get('end_date')} does not match job {meta['period_end']}")
    job_store.save_review_json(job_id, data)
    return {"ok": True, "schema_version": data.get("schema_version")}


@router.post("/jobs/{job_id}/extract", dependencies=[Depends(require_role("manager"))])
async def run_extract(job_id: str):
    try:
        meta = job_store.load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Import job not found")
    provider = get_extraction_provider()
    job_store.set_job_status(job_id, "extracting")
    try:
        review = provider.extract(job_id, meta["period_start"], meta["period_end"])
        ok, errs = validate_review_payload(review)
        if not ok:
            raise ValueError("; ".join(errs))
        job_store.save_review_json(job_id, review)
        meta = job_store.load_job(job_id)
        meta["extraction_provider"] = provider.name
        job_store._write_meta(job_id, meta)
        return {"status": "review_ready", "provider": provider.name}
    except Exception as exc:
        job_store.set_job_status(job_id, "review_ready" if (job_store.job_dir(job_id) / "review.json").is_file() else "uploaded", str(exc))
        raise HTTPException(502, detail=str(exc))


@router.get("/jobs/{job_id}/preview", dependencies=[Depends(get_current_user)])
async def get_preview(job_id: str):
    try:
        meta = job_store.load_job(job_id)
        review = job_store.load_review_json(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc))
    preview = build_preview(review, meta["period_start"], meta["period_end"])
    meta["last_preview_at"] = preview.get("period")
    job_store._write_meta(job_id, meta)
    return {"job": meta, "preview": preview}


@router.post("/jobs/{job_id}/apply", dependencies=[Depends(require_role("manager"))])
async def apply_import(
    job_id: str,
    dry_run: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    try:
        meta = job_store.load_job(job_id)
        review = job_store.load_review_json(job_id)
    except FileNotFoundError:
        raise HTTPException(404, "Job or review JSON not found")
    if meta.get("status") == "cancelled":
        raise HTTPException(400, "Job cancelled")

    report = run_import_from_review(
        review,
        period_start=meta["period_start"],
        period_end=meta["period_end"],
        apply=not dry_run,
        snapshot_dir=SNAPSHOT_DIR,
    )
    report["applied_by"] = current_user.get("email") or current_user.get("sub")
    report["dry_run"] = dry_run
    name = job_store.save_apply_report(job_id, report)
    if not dry_run:
        job_store.set_job_status(job_id, "completed")
    return {"report_file": name, "stats": report["stats"], "dry_run": dry_run}


@router.get("/config", dependencies=[Depends(get_current_user)])
async def import_config():
    provider = get_extraction_provider()
    return {
        "extraction_provider": provider.name,
        "requires_review_json_upload": provider.name == "manual_review_json",
        "env": "NEC_IMPORT_EXTRACTION_PROVIDER",
    }
