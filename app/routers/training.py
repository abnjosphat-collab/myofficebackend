# app/routers/training.py - Training & Certification register.
#
# Records live in the `training_certifications` table (supabase_migration_training_certifications.sql)
# and certificate files in the Document Hub's storage bucket under `training/`. Until 2026-10-09 this
# router kept a module-level Python list seeded with mock people ("John Doe", ...), so every restart or
# redeploy wiped what users had entered and brought the mock rows back, and "uploaded" certificates
# were only an invented URL with no stored file. The API shape is unchanged, so the page needs no edit.
#
# Status (Valid / Due Soon / Expired) is computed on every read from the expiry date, never stored,
# so it cannot go stale.

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.aggregation import count_by
from app.auth import get_current_user, require_role
from app.supabase_client import supabase
from app.uploads import read_and_validate_upload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["Training & Certification"])

TABLE = "training_certifications"
BUCKET = "ams-documents"  # shared with the Document Hub; certificates sit under training/
CERTIFICATE_EXTS = {"pdf", "jpg", "jpeg", "png", "webp", "heic"}
MAX_CERTIFICATE_BYTES = 15 * 1024 * 1024


def check_status(expiry_date: date) -> str:
    """Compliance status from the expiry date: Expired, Due Soon (within 90 days) or Valid."""
    today = date.today()
    if expiry_date < today:
        return "Expired"
    if expiry_date <= today + timedelta(days=90):
        return "Due Soon"
    return "Valid"


class CertificateRecord(BaseModel):
    """One certification as the page reads it."""
    id: str
    employee_id: str
    employee_name: str
    department: str
    certification_name: str
    expiry_date: date
    required_refresher: str
    certificate_url: Optional[str] = None
    status: str


def _record(row: dict[str, Any]) -> CertificateRecord:
    expiry = row["expiry_date"]
    expiry = expiry if isinstance(expiry, date) else date.fromisoformat(str(expiry)[:10])
    return CertificateRecord(
        id=str(row["id"]),
        employee_id=row.get("employee_id") or "",
        employee_name=row.get("employee_name") or "",
        department=row.get("department") or "",
        certification_name=row.get("certification_name") or "",
        expiry_date=expiry,
        required_refresher=row.get("required_refresher") or "",
        certificate_url=row.get("certificate_url") or None,
        status=check_status(expiry),
    )


def _all_records() -> List[CertificateRecord]:
    try:
        rows = supabase.table(TABLE).select("*").order("expiry_date").execute().data or []
    except Exception as e:
        logger.error("training list failed: %s", e)
        raise HTTPException(status_code=502, detail="Training records could not be read from the database.")
    return [_record(r) for r in rows]


def _one_row(record_id: str) -> dict[str, Any]:
    try:
        rows = supabase.table(TABLE).select("*").eq("id", record_id).limit(1).execute().data or []
    except Exception as e:
        logger.error("training read failed: %s", e)
        raise HTTPException(status_code=502, detail="Training record could not be read from the database.")
    if not rows:
        raise HTTPException(status_code=404, detail="Certification record not found")
    return rows[0]


async def _store_certificate(file: UploadFile) -> tuple[str, str]:
    """Saves the certificate file; returns (storage_path, public_url)."""
    content = await read_and_validate_upload(file, max_bytes=MAX_CERTIFICATE_BYTES, allowed_exts=CERTIFICATE_EXTS)
    name = file.filename or "certificate"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "pdf"
    path = f"training/{uuid.uuid4()}.{ext}"
    try:
        supabase.storage.from_(BUCKET).upload(path, content, {"content-type": file.content_type or "application/octet-stream"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Certificate upload failed: {e}")
    try:
        url = supabase.storage.from_(BUCKET).get_public_url(path)
    except Exception as e:
        logger.error("get_public_url failed for %s: %s", path, e)
        url = ""
    return path, url


def _remove_file(path: Optional[str]) -> None:
    if not path:
        return
    try:
        supabase.storage.from_(BUCKET).remove([path])
    except Exception as e:  # the record change already succeeded; an orphaned file is logged, not fatal
        logger.error("certificate file cleanup failed for %s: %s", path, e)


# --- Records ---

@router.get("/", response_model=List[CertificateRecord], dependencies=[Depends(get_current_user)])
async def get_all_certifications():
    """All certification records, soonest expiry first."""
    return _all_records()


@router.post("/", response_model=CertificateRecord)
async def create_new_certification(
    employee_id: str = Form(...),
    employee_name: str = Form(...),
    department: str = Form(...),
    certification_name: str = Form(...),
    expiry_date: date = Form(..., description="Format: YYYY-MM-DD"),
    required_refresher: str = Form(...),
    certificate_file: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """Adds a certification, storing its certificate file when one is attached."""
    path, url = (await _store_certificate(certificate_file)) if certificate_file else (None, None)
    row = {
        "employee_id": employee_id.strip(), "employee_name": employee_name.strip(), "department": department.strip(),
        "certification_name": certification_name.strip(), "expiry_date": expiry_date.isoformat(),
        "required_refresher": required_refresher.strip(), "certificate_url": url, "certificate_path": path,
        "created_by": current_user.get("email"),
    }
    try:
        saved = supabase.table(TABLE).insert(row).execute().data or []
    except Exception as e:
        _remove_file(path)
        raise HTTPException(status_code=500, detail=f"The certification was not saved: {e}")
    if not saved:
        _remove_file(path)
        raise HTTPException(status_code=500, detail="The certification was not saved.")
    return _record(saved[0])


@router.get("/{record_id}", response_model=CertificateRecord, dependencies=[Depends(get_current_user)])
async def get_certification(record_id: str):
    """One certification record."""
    return _record(_one_row(record_id))


@router.put("/{record_id}", response_model=CertificateRecord)
async def update_certification(
    record_id: str,
    employee_id: str = Form(None),
    employee_name: str = Form(None),
    department: str = Form(None),
    certification_name: str = Form(None),
    expiry_date: date = Form(None),
    required_refresher: str = Form(None),
    certificate_file: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """Changes the fields sent; a new certificate file replaces the old one."""
    existing = _one_row(record_id)
    patch: dict[str, Any] = {}
    for key, value in (("employee_id", employee_id), ("employee_name", employee_name), ("department", department),
                       ("certification_name", certification_name), ("required_refresher", required_refresher)):
        if value is not None:
            patch[key] = value.strip()
    if expiry_date is not None:
        patch["expiry_date"] = expiry_date.isoformat()
    new_path = None
    if certificate_file:
        new_path, url = await _store_certificate(certificate_file)
        patch["certificate_path"], patch["certificate_url"] = new_path, url
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        saved = supabase.table(TABLE).update(patch).eq("id", record_id).execute().data or []
    except Exception as e:
        _remove_file(new_path)
        raise HTTPException(status_code=500, detail=f"The certification was not updated: {e}")
    if new_path:
        _remove_file(existing.get("certificate_path"))
    return _record(saved[0] if saved else {**existing, **patch})


@router.delete("/{record_id}")
async def delete_certification(record_id: str, current_user: dict = Depends(require_role("manager"))):
    """Deletes a certification and its stored certificate file."""
    existing = _one_row(record_id)
    try:
        supabase.table(TABLE).delete().eq("id", record_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"The certification was not deleted: {e}")
    _remove_file(existing.get("certificate_path"))
    return {"message": "Certification record deleted successfully"}


# --- Compliance reporting ---

@router.get("/reports/compliance_rate", dependencies=[Depends(get_current_user)])
async def get_compliance_rate():
    """Share of tracked certifications that have not expired."""
    records = _all_records()
    total = len(records)
    if total == 0:
        return {"compliance_rate": 100.0, "total_tracked": 0, "non_compliant": 0}
    expired = sum(1 for r in records if r.status == "Expired")
    return {"compliance_rate": round((total - expired) / total * 100, 2), "total_tracked": total, "non_compliant": expired}


@router.get("/reports/due_refreshers", dependencies=[Depends(get_current_user)])
async def get_due_refreshers():
    """The three refresher courses most people need next (expired certificates excluded)."""
    due = [r.required_refresher for r in _all_records() if r.status != "Expired" and r.required_refresher not in ("N/A", "", None)]
    result = [{"refresher": k, "employees_due": v} for k, v in count_by(due, lambda x: x).items()]
    return sorted(result, key=lambda x: x["employees_due"], reverse=True)[:3]


@router.get("/employee/{employee_id}", response_model=List[CertificateRecord], dependencies=[Depends(get_current_user)])
async def get_employee_certifications(employee_id: str):
    """One employee's certifications."""
    return [r for r in _all_records() if r.employee_id == employee_id]


@router.get("/alerts/expiring", dependencies=[Depends(get_current_user)])
async def get_expiring_certifications(days: int = 90):
    """Certifications expiring within `days`, soonest first."""
    today = date.today()
    expiring = []
    for r in _all_records():
        left = (r.expiry_date - today).days
        if 0 <= left <= days:
            expiring.append({**r.model_dump(), "days_until_expiry": left})
    expiring.sort(key=lambda x: x["days_until_expiry"])
    return {"days_threshold": days, "count": len(expiring), "certifications": expiring}


@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_training_stats():
    """Counts by status and department, and the compliance rate."""
    records = _all_records()
    total = len(records)
    status_counts = count_by(records, lambda c: c.status)
    return {
        "totalCertifications": total,
        "statusDistribution": status_counts,
        "departmentDistribution": count_by(records, lambda c: c.department),
        "complianceRate": round((total - status_counts.get("Expired", 0)) / total * 100, 2) if total else 100.0,
    }
