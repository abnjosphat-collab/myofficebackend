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

from app.clock import today_harare
from app.aggregation import count_by
from app.auth import get_current_user, require_role
from app.db_helpers import response_rows
from app.supabase_client import supabase
from app.uploads import read_and_validate_upload
from app.errors import server_error
from app.scope import apply_list_scope, assert_row_visible, person_ids_for, stamp_create, viewer_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["Training & Certification"])

TABLE = "training_certifications"
BUCKET = "ams-documents"  # shared with the Document Hub; certificates sit under training/
CERTIFICATE_EXTS = {"pdf", "jpg", "jpeg", "png", "webp", "heic"}
MAX_CERTIFICATE_BYTES = 15 * 1024 * 1024


def check_status(expiry_date: date) -> str:
    """Compliance status from the expiry date: Expired, Due Soon (within 90 days) or Valid."""
    today = today_harare()
    if expiry_date < today:
        return "Expired"
    if expiry_date <= today + timedelta(days=90):
        return "Due Soon"
    return "Valid"


class CertificateRecord(BaseModel):
    """One certification as the page reads it.

    `employee_id` is the authoritative link to the employee register (the
    personnel number, uppercased). `snapshot_*` freeze the register values
    seen at training time; the live register stays the source of current
    details. `department` is kept as the legacy at-training display so old
    rows read unchanged.
    """
    id: str
    employee_id: str
    employee_name: str
    department: str
    certification_name: str
    expiry_date: date
    required_refresher: str
    certificate_url: Optional[str] = None
    status: str
    provider: str = ""
    training_date: Optional[date] = None
    snapshot_designation: str = ""
    snapshot_section: str = ""
    snapshot_department: str = ""
    group_evidence_url: Optional[str] = None
    batch_id: Optional[str] = None


def _training_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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
        provider=row.get("provider") or "",
        training_date=_training_date(row.get("training_date")),
        snapshot_designation=row.get("snapshot_designation") or "",
        snapshot_section=row.get("snapshot_section") or "",
        snapshot_department=row.get("snapshot_department") or "",
        group_evidence_url=row.get("group_evidence_url") or None,
        batch_id=row.get("batch_id") or None,
    )


def _normalize_employee_id(value: Any) -> str:
    return str(value or "").strip().upper()


def _register_row(employee_id: str) -> Optional[dict[str, Any]]:
    """The exact register row for a personnel number, or None.

    Only an exact (case-insensitive, whitespace-tolerant) match links: names
    are never guessed, and an ambiguous number links nothing.
    """
    normalized = _normalize_employee_id(employee_id)
    if not normalized:
        return None
    try:
        rows = response_rows(
            supabase.table("employees").select("*").eq("employee_id", normalized).limit(2).execute()
        )
    except Exception as e:
        logger.error("training employee lookup failed: %s", e)
        return None
    if len(rows) != 1:
        if len(rows) > 1:
            logger.error("training employee lookup ambiguous for %s: linking nothing", normalized)
        return None
    return rows[0]


def _snapshots_for(employee_id: str, submitted_name: str, submitted_department: str) -> dict[str, str]:
    """At-training display values: official register values on an exact match,
    otherwise the submitted values (a legacy unlinked row). Later register
    edits never rewrite these; they stay the historical fact."""
    row = _register_row(employee_id)
    if row is None:
        return {
            "employee_name": (submitted_name or "").strip(),
            "department": (submitted_department or "").strip(),
            "snapshot_designation": "",
            "snapshot_section": "",
            "snapshot_department": (submitted_department or "").strip(),
        }
    name = " ".join(f"{row.get('first_name') or ''} {row.get('last_name') or ''}".split())
    department = str(row.get("department") or "").strip()
    return {
        "employee_name": name or (submitted_name or "").strip(),
        "department": department or (submitted_department or "").strip(),
        "snapshot_designation": str(row.get("designation") or "").strip(),
        "snapshot_section": str(row.get("section") or "").strip(),
        "snapshot_department": department,
    }


def _duplicate_of(employee_id: str, certification_name: str, training_date: Optional[date],
                  exclude_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """An existing row for the same person, certification and training date.

    Date-keyed: a different training date (or expiry) is a legitimate renewal
    or repeat, never a duplicate. Rows without a training date predate this
    guard and never block, so legacy workflows keep working.
    """
    if not isinstance(training_date, date):
        # Direct coroutine calls in tests leave FastAPI's Form() marker as the
        # default; only a real date guards.
        training_date = _training_date(training_date) if isinstance(training_date, str) else None
    if training_date is None:
        return None
    normalized = _normalize_employee_id(employee_id)
    wanted = (certification_name or "").strip().lower()
    if not normalized or not wanted:
        return None
    try:
        rows = response_rows(
            supabase.table(TABLE).select("*")
            .eq("employee_id", normalized)
            .eq("training_date", training_date.isoformat())
            .execute()
        )
    except Exception as e:
        logger.error("training duplicate check failed: %s", e)
        raise HTTPException(status_code=502, detail="Training records could not be read from the database.")
    for row in rows:
        if exclude_id is not None and str(row.get("id")) == str(exclude_id):
            continue
        if str(row.get("certification_name") or "").strip().lower() == wanted:
            return row
    return None


def _all_records(user=None, person_ids=None) -> List[CertificateRecord]:
    try:
        q = apply_list_scope(supabase.table(TABLE).select("*").order("expiry_date"),
                             user, TABLE, supabase, person_ids)
        rows = response_rows(q.execute())
    except Exception as e:
        logger.error("training list failed: %s", e)
        raise HTTPException(status_code=502, detail="Training records could not be read from the database.")
    return [_record(r) for r in rows]


def _one_row(record_id: str) -> dict[str, Any]:
    try:
        rows = response_rows(supabase.table(TABLE).select("*").eq("id", record_id).limit(1).execute())
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
        raise server_error(e, "Certificate upload")
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

@router.get("/", response_model=List[CertificateRecord])
async def get_all_certifications(current_user: dict = Depends(get_current_user)):
    """All certification records, soonest expiry first."""
    return _all_records(current_user, person_ids_for(supabase, current_user))


@router.post("/", response_model=CertificateRecord)
async def create_new_certification(
    employee_id: str = Form(...),
    employee_name: str = Form(...),
    department: str = Form(...),
    certification_name: str = Form(...),
    expiry_date: date = Form(..., description="Format: YYYY-MM-DD"),
    required_refresher: str = Form(...),
    provider: str = Form(""),
    training_date: Optional[date] = Form(None),
    certificate_file: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """Adds a certification, storing its certificate file when one is attached.

    On an exact register match the name, department and snapshots come from
    the register, not the submitted text. A repeat of the same person,
    certification and training date is refused (409); a different date is a
    legitimate renewal and is accepted.
    """
    normalized_id = _normalize_employee_id(employee_id)
    training_date = training_date if isinstance(training_date, date) else None
    provider = provider if isinstance(provider, str) else ""
    if viewer_scope(current_user) is not None:
        stamp_create(current_user, TABLE, {"employee_id": normalized_id},
                     supabase, person_ids_for(supabase, current_user))
    if _duplicate_of(normalized_id, certification_name, training_date) is not None:
        raise HTTPException(
            status_code=409,
            detail="This certification was already recorded for this employee on that training date. "
                   "A renewal uses a different training date.",
        )
    snapshot = _snapshots_for(normalized_id, employee_name, department)
    path, url = (await _store_certificate(certificate_file)) if certificate_file else (None, None)
    row = {
        "employee_id": normalized_id, "employee_name": snapshot["employee_name"],
        "department": snapshot["department"],
        "certification_name": certification_name.strip(), "expiry_date": expiry_date.isoformat(),
        "required_refresher": required_refresher.strip(), "certificate_url": url, "certificate_path": path,
        "provider": (provider or "").strip(),
        "training_date": training_date.isoformat() if training_date else None,
        "snapshot_designation": snapshot["snapshot_designation"],
        "snapshot_section": snapshot["snapshot_section"],
        "snapshot_department": snapshot["snapshot_department"],
        "created_by": current_user.get("email"),
    }
    try:
        saved = response_rows(supabase.table(TABLE).insert(row).execute())
    except Exception as e:
        _remove_file(path)
        raise server_error(e, "Saving certification")
    if not saved:
        _remove_file(path)
        raise HTTPException(status_code=500, detail="The certification was not saved.")
    return _record(saved[0])


class BulkTrainingItem(BaseModel):
    """One person in a bulk certification entry; any field left out inherits
    the shared value sent alongside the batch."""
    employee_id: str
    employee_name: Optional[str] = None
    expiry_date: Optional[date] = None
    required_refresher: Optional[str] = None
    provider: Optional[str] = None
    training_date: Optional[date] = None


class BulkTrainingRequest(BaseModel):
    certification_name: str
    expiry_date: date
    training_date: date
    provider: str = ""
    required_refresher: str = ""
    group_evidence_url: Optional[str] = None
    batch_id: Optional[str] = None
    items: List[BulkTrainingItem]


class BulkItemResult(BaseModel):
    employee_id: str
    outcome: str  # created | skipped | failed
    record_id: Optional[str] = None
    reason: Optional[str] = None
    uncertain: bool = False


class BulkTrainingResponse(BaseModel):
    batch_id: Optional[str] = None
    total: int
    created: int
    skipped: int
    failed: int
    results: List[BulkItemResult]


@router.post("/bulk", response_model=BulkTrainingResponse)
async def create_bulk_certifications(payload: BulkTrainingRequest,
                                     current_user: dict = Depends(get_current_user)):
    """Records one shared certification for several employees in one request.

    The same person + certification + training date twice in (or across)
    batches is reported as `skipped`, never stored twice; a different date
    is a renewal and is stored. Each entry stands on its own: one person's
    failure never blocks the rest, and a retry after a partial failure only
    replays the entries that are still missing. A shared attendance sheet
    link is stored as group evidence, never as anyone's certificate.
    """
    if not payload.items:
        raise HTTPException(status_code=422, detail="Add at least one employee.")
    if len(payload.items) > 200:
        raise HTTPException(status_code=422, detail="A batch holds at most 200 employees.")
    if payload.group_evidence_url is not None:
        evidence = payload.group_evidence_url.strip()
        if evidence and not evidence.startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="The group evidence link must be an http(s) URL.")
    scoped = viewer_scope(current_user) is not None
    person_ids = person_ids_for(supabase, current_user) if scoped else None
    allowed = {str(v).strip().upper() for v in (person_ids["register"] if person_ids else set())}
    batch_id = (payload.batch_id or "").strip() or str(uuid.uuid4())
    cert_name = payload.certification_name.strip()
    if not cert_name:
        raise HTTPException(status_code=422, detail="Enter the certification name.")
    results: List[BulkItemResult] = []
    seen: set[tuple[str, str, str]] = set()
    for item in payload.items:
        normalized = _normalize_employee_id(item.employee_id)
        if not normalized:
            results.append(BulkItemResult(employee_id="", outcome="failed",
                                          reason="Enter the employee ID.", uncertain=False))
            continue
        eff_date = item.training_date or payload.training_date
        eff_expiry = item.expiry_date or payload.expiry_date
        key = (normalized, cert_name.lower(), eff_date.isoformat())
        if key in seen:
            results.append(BulkItemResult(employee_id=normalized, outcome="skipped",
                                          reason="Included twice in this batch; recorded once.", uncertain=False))
            continue
        seen.add(key)
        if scoped and normalized not in allowed:
            results.append(BulkItemResult(employee_id=normalized, outcome="failed",
                                          reason="Outside your visible employees; not recorded.", uncertain=False))
            continue
        try:
            if _duplicate_of(normalized, cert_name, eff_date) is not None:
                results.append(BulkItemResult(employee_id=normalized, outcome="skipped",
                                              reason="Already recorded for this employee on that training date; "
                                                     "not stored again.", uncertain=False))
                continue
        except HTTPException as e:
            results.append(BulkItemResult(employee_id=normalized, outcome="failed",
                                          reason=e.detail if isinstance(e.detail, str) else "Could not be checked.",
                                          uncertain=True))
            continue
        snapshot = _snapshots_for(normalized, item.employee_name or "", "")
        row = {
            "employee_id": normalized, "employee_name": snapshot["employee_name"],
            "department": snapshot["snapshot_department"],
            "certification_name": cert_name, "expiry_date": eff_expiry.isoformat(),
            "required_refresher": (item.required_refresher if item.required_refresher is not None
                                   else payload.required_refresher).strip(),
            "provider": (item.provider if item.provider is not None else payload.provider).strip(),
            "training_date": eff_date.isoformat(),
            "snapshot_designation": snapshot["snapshot_designation"],
            "snapshot_section": snapshot["snapshot_section"],
            "snapshot_department": snapshot["snapshot_department"],
            "certificate_url": None, "certificate_path": None,
            "group_evidence_url": (payload.group_evidence_url or "").strip() or None,
            "batch_id": batch_id,
            "created_by": current_user.get("email"),
        }
        try:
            saved = response_rows(supabase.table(TABLE).insert(row).execute())
        except HTTPException as e:
            results.append(BulkItemResult(employee_id=normalized, outcome="failed",
                                          reason=e.detail if isinstance(e.detail, str) else "Not saved.",
                                          uncertain=True))
            continue
        except Exception as e:
            logger.error("training bulk save failed for %s: %s", normalized, e)
            results.append(BulkItemResult(employee_id=normalized, outcome="failed",
                                          reason="No save confirmation received; it may already be recorded. "
                                                 "Check the register before retrying.", uncertain=True))
            continue
        if not saved:
            results.append(BulkItemResult(employee_id=normalized, outcome="failed",
                                          reason="The certification was not saved.", uncertain=True))
            continue
        results.append(BulkItemResult(employee_id=normalized, outcome="created",
                                      record_id=str(saved[0].get("id"))))
    created = sum(1 for r in results if r.outcome == "created")
    skipped = sum(1 for r in results if r.outcome == "skipped")
    failed = sum(1 for r in results if r.outcome == "failed")
    return BulkTrainingResponse(batch_id=batch_id, total=len(results), created=created,
                                skipped=skipped, failed=failed, results=results)


@router.get("/{record_id}", response_model=CertificateRecord)
async def get_certification(record_id: str, current_user: dict = Depends(get_current_user)):
    """One certification record."""
    row = _one_row(record_id)
    assert_row_visible(current_user, TABLE, row, supabase,
                       person_ids_for(supabase, current_user), detail="Certification record not found")
    return _record(row)


@router.put("/{record_id}", response_model=CertificateRecord)
async def update_certification(
    record_id: str,
    employee_id: str = Form(None),
    employee_name: str = Form(None),
    department: str = Form(None),
    certification_name: str = Form(None),
    expiry_date: date = Form(None),
    required_refresher: str = Form(None),
    provider: str = Form(None),
    training_date: date = Form(None),
    certificate_file: Optional[UploadFile] = File(None),
    current_user: dict = Depends(get_current_user),
):
    """Changes the fields sent; a new certificate file replaces the old one.

    Moving a record onto an exact register match refreshes its snapshots from
    the register. Historical snapshots are otherwise left alone: editing other
    fields never rewrites them.
    """
    existing = _one_row(record_id)
    provider = provider if isinstance(provider, str) else None
    training_date = training_date if isinstance(training_date, date) else None
    if viewer_scope(current_user) is not None:
        person_ids = person_ids_for(supabase, current_user)
        assert_row_visible(current_user, TABLE, existing, supabase, person_ids,
                           detail="Certification record not found")
        assert person_ids is not None  # person_ids_for never returns None for a scoped viewer
        if employee_id is not None and _normalize_employee_id(employee_id) not in {str(v).strip().upper() for v in person_ids["register"]}:
            raise HTTPException(status_code=404, detail="Certification record not found")
    patch: dict[str, Any] = {}
    for key, value in (("certification_name", certification_name), ("required_refresher", required_refresher),
                       ("provider", provider)):
        if value is not None:
            patch[key] = value.strip()
    if expiry_date is not None:
        patch["expiry_date"] = expiry_date.isoformat()
    if training_date is not None:
        patch["training_date"] = training_date.isoformat()
    if employee_id is not None:
        normalized = _normalize_employee_id(employee_id)
        snapshot = _snapshots_for(
            normalized,
            employee_name if employee_name is not None else str(existing.get("employee_name") or ""),
            department if department is not None else str(existing.get("department") or ""),
        )
        patch["employee_id"] = normalized
        patch["employee_name"] = snapshot["employee_name"]
        patch["department"] = snapshot["department"]
        patch["snapshot_designation"] = snapshot["snapshot_designation"]
        patch["snapshot_section"] = snapshot["snapshot_section"]
        patch["snapshot_department"] = snapshot["snapshot_department"]
    elif employee_name is not None or department is not None:
        # Free-text correction on an unlinked row only: linked rows keep the
        # register's official values until their employee_id is re-pointed.
        if _register_row(str(existing.get("employee_id") or "")) is None:
            if employee_name is not None:
                patch["employee_name"] = employee_name.strip()
            if department is not None:
                patch["department"] = department.strip()
                patch["snapshot_department"] = department.strip()
    eff_employee = patch.get("employee_id", str(existing.get("employee_id") or ""))
    eff_cert = patch.get("certification_name", str(existing.get("certification_name") or ""))
    eff_date_raw = patch.get("training_date", existing.get("training_date"))
    eff_date = eff_date_raw if isinstance(eff_date_raw, date) else _training_date(eff_date_raw)
    if _duplicate_of(eff_employee, eff_cert, eff_date, exclude_id=record_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Another record already holds this certification for this employee on that training date.",
        )
    new_path = None
    if certificate_file:
        new_path, url = await _store_certificate(certificate_file)
        patch["certificate_path"], patch["certificate_url"] = new_path, url
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        saved = response_rows(supabase.table(TABLE).update(patch).eq("id", record_id).execute())
    except Exception as e:
        _remove_file(new_path)
        raise server_error(e, "Updating certification")
    if not saved:  # deleted by someone else since it was read: say so instead of reporting a save that did not happen
        _remove_file(new_path)
        raise HTTPException(status_code=404, detail="Certification record not found; it may have just been deleted.")
    if new_path:
        _remove_file(existing.get("certificate_path"))
    return _record(saved[0])


@router.delete("/{record_id}")
async def delete_certification(record_id: str, current_user: dict = Depends(require_role("manager"))):
    """Deletes a certification and its stored certificate file."""
    existing = _one_row(record_id)
    if viewer_scope(current_user) is not None:
        assert_row_visible(current_user, TABLE, existing, supabase,
                           person_ids_for(supabase, current_user), detail="Certification record not found")
    try:
        supabase.table(TABLE).delete().eq("id", record_id).execute()
    except Exception as e:
        raise server_error(e, "Deleting certification")
    _remove_file(existing.get("certificate_path"))
    return {"message": "Certification record deleted successfully"}


# --- Compliance reporting ---

@router.get("/reports/compliance_rate")
async def get_compliance_rate(current_user: dict = Depends(get_current_user)):
    """Share of tracked certifications that have not expired."""
    records = _all_records(current_user, person_ids_for(supabase, current_user))
    total = len(records)
    if total == 0:
        return {"compliance_rate": 100.0, "total_tracked": 0, "non_compliant": 0}
    expired = sum(1 for r in records if r.status == "Expired")
    return {"compliance_rate": round((total - expired) / total * 100, 2), "total_tracked": total, "non_compliant": expired}


@router.get("/reports/due_refreshers")
async def get_due_refreshers(current_user: dict = Depends(get_current_user)):
    """The three refresher courses most people need next (expired certificates excluded)."""
    due = [r.required_refresher for r in _all_records(current_user, person_ids_for(supabase, current_user)) if r.status != "Expired" and r.required_refresher not in ("N/A", "", None)]
    result = [{"refresher": k, "employees_due": v} for k, v in count_by(due, lambda x: x).items()]
    return sorted(result, key=lambda x: x["employees_due"], reverse=True)[:3]


@router.get("/employee/{employee_id}", response_model=List[CertificateRecord])
async def get_employee_certifications(employee_id: str, current_user: dict = Depends(get_current_user)):
    """One employee's certifications."""
    return [r for r in _all_records(current_user, person_ids_for(supabase, current_user)) if r.employee_id == employee_id]


@router.get("/alerts/expiring")
async def get_expiring_certifications(days: int = 90, current_user: dict = Depends(get_current_user)):
    """Certifications expiring within `days`, soonest first."""
    today = today_harare()
    expiring = []
    for r in _all_records(current_user, person_ids_for(supabase, current_user)):
        left = (r.expiry_date - today).days
        if 0 <= left <= days:
            expiring.append({**r.model_dump(), "days_until_expiry": left})
    expiring.sort(key=lambda x: x["days_until_expiry"])
    return {"days_threshold": days, "count": len(expiring), "certifications": expiring}


@router.get("/stats/summary")
async def get_training_stats(current_user: dict = Depends(get_current_user)):
    """Counts by status and department, and the compliance rate."""
    records = _all_records(current_user, person_ids_for(supabase, current_user))
    total = len(records)
    status_counts = count_by(records, lambda c: c.status)
    return {
        "totalCertifications": total,
        "statusDistribution": status_counts,
        "departmentDistribution": count_by(records, lambda c: c.department),
        "complianceRate": round((total - status_counts.get("Expired", 0)) / total * 100, 2) if total else 100.0,
    }
