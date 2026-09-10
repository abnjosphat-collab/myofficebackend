"""SOP Library router — sop_documents (+ sop_revisions).

Hand-written rather than CrudRouter: this table has three behaviours a generic
CRUD shape doesn't cover — a revision snapshot written on every successful save
(app/db/design intent: sop_revisions is an append-only audit trail, never edited),
soft-delete/restore instead of a real DELETE (no hard-delete endpoint exists at
all), and a write-time role split (create/edit/archive need manager+, but
marking an SOP the operating standard — status -> 'effective' — needs admin+,
the same *conditional* role pattern leaves.py/overtime.py already use via
require_role_if_status_in).

The status/classification/risk-tier vocabulary and the section list below are
modeled directly on Ozech's own SOP Governance Manual and Master SOP Template
(docs/OZECH SOPS), not invented: status mirrors the document lifecycle table
(Draft/Pilot/Effective/Superseded/Retired), classification mirrors the
Internal/Confidential/Restricted scale, risk_tier mirrors the 1/2/3 routine/
material/critical tiers, and the section keys mirror the manual's "mandatory
SOP content" list. Only code/title/department/owner are required — every
section and every governance metadata field beyond that is optional, since a
Draft is expected to be filled in incrementally, not all at once.

Run backend/supabase_migration_sop_library.sql before using this router.
"""
from datetime import datetime, date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel, Field, validator

from app.supabase_client import supabase, rows as db_rows, one_row
from app.auth import get_current_user, require_role, require_role_if_status_in
from app.db_helpers import or_ilike, get_or_404
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sops", tags=["SOPs"])

STATUSES = ("draft", "pilot", "effective", "superseded", "retired")
CLASSIFICATIONS = ("Internal", "Confidential", "Restricted")
RISK_TIERS = (1, 2, 3)


def _validate_status(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    return v


def _validate_classification(v: Optional[str]) -> Optional[str]:
    if v is not None and v not in CLASSIFICATIONS:
        raise ValueError(f"classification must be one of {CLASSIFICATIONS}")
    return v


def _validate_risk_tier(v: Optional[int]) -> Optional[int]:
    if v is not None and v not in RISK_TIERS:
        raise ValueError(f"risk_tier must be one of {RISK_TIERS}")
    return v


class SopSections(BaseModel):
    purpose: str = ""
    scope_exclusions: str = ""
    definitions: str = ""
    trigger_outcome: str = ""
    roles_responsibilities: str = ""
    inputs_dependencies: str = ""
    procedure: str = ""
    controls: str = ""
    exceptions_escalation: str = ""
    records_retention: str = ""
    measures_review: str = ""
    training: str = ""


class SopCreate(BaseModel):
    code: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    summary: str = ""
    status: str = "draft"
    classification: str = "Internal"
    risk_tier: Optional[int] = None
    owner: str = Field(..., min_length=1)
    approver: Optional[str] = None
    supersedes: Optional[str] = None
    version: str = "0.1"
    effective_date: Optional[date] = None
    next_review_date: Optional[date] = None
    tags: List[str] = Field(default_factory=list)
    sections: SopSections = Field(default_factory=SopSections)
    change_note: str = "Initial draft"

    _validate_status = validator("status", allow_reuse=True)(_validate_status)
    _validate_classification = validator("classification", allow_reuse=True)(_validate_classification)
    _validate_risk_tier = validator("risk_tier", allow_reuse=True)(_validate_risk_tier)


class SopUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1)
    department: Optional[str] = Field(None, min_length=1)
    summary: Optional[str] = None
    status: Optional[str] = None
    classification: Optional[str] = None
    risk_tier: Optional[int] = None
    owner: Optional[str] = Field(None, min_length=1)
    approver: Optional[str] = None
    supersedes: Optional[str] = None
    version: Optional[str] = None
    effective_date: Optional[date] = None
    next_review_date: Optional[date] = None
    tags: Optional[List[str]] = None
    sections: Optional[SopSections] = None
    change_note: str = ""

    _validate_status = validator("status", allow_reuse=True)(_validate_status)
    _validate_classification = validator("classification", allow_reuse=True)(_validate_classification)
    _validate_risk_tier = validator("risk_tier", allow_reuse=True)(_validate_risk_tier)


def _serialize(row: dict) -> dict:
    for k, v in list(row.items()):
        if isinstance(v, (date, datetime)):
            row[k] = v.isoformat()
    return row


def _next_revision_number(sop_id: str) -> int:
    r = (supabase.table("sop_revisions")
         .select("revision_number")
         .eq("sop_id", sop_id)
         .order("revision_number", desc=True)
         .limit(1)
         .execute())
    existing = db_rows(r)
    return (existing[0]["revision_number"] + 1) if existing else 1


def _snapshot(sop_id: str, doc: dict, change_note: str, user: dict):
    try:
        supabase.table("sop_revisions").insert({
            "sop_id": sop_id,
            "revision_number": _next_revision_number(sop_id),
            "snapshot": doc,
            "change_note": change_note.strip() or "No change note provided",
            "author_email": user.get("email", ""),
            "author_id": user.get("user_id", ""),
        }).execute()
    except Exception as e:
        # The document itself already saved successfully above — a failure here
        # would otherwise turn a good save into a reported error. Log loudly
        # instead of hiding it as a silent no-op (see myoffice-silent-failure
        # convention), but don't fail the request over a history-row write.
        logger.error("sop_revisions insert failed for sop %s: %s", sop_id, e)


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def list_sops(
    search: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    owner: Optional[str] = None,
):
    try:
        q = supabase.table("sop_documents").select("*").is_("deleted_at", "null")
        if search:
            q = q.or_(or_ilike(["code", "title", "summary"], search))
        if department:
            q = q.eq("department", department)
        if status:
            q = q.eq("status", status)
        if owner:
            q = q.eq("owner", owner)
        r = q.order("updated_at", desc=True).execute()
        return [_serialize(row) for row in db_rows(r)]
    except Exception as e:
        logger.error("list_sops error: %s", e)
        raise HTTPException(500, "Could not load the SOP library right now.")


@router.get("/archived", dependencies=[Depends(require_role("manager"))])
async def list_archived_sops():
    try:
        r = (supabase.table("sop_documents")
             .select("*")
             .not_.is_("deleted_at", "null")
             .order("updated_at", desc=True)
             .execute())
        return [_serialize(row) for row in db_rows(r)]
    except Exception as e:
        logger.error("list_archived_sops error: %s", e)
        raise HTTPException(500, "Could not load archived SOPs right now.")


# ── Get one (+ revision history) ─────────────────────────────────────────────

@router.get("/{sop_id}", dependencies=[Depends(get_current_user)])
async def get_sop(sop_id: str):
    doc = get_or_404(supabase, "sop_documents", sop_id, detail="SOP not found")
    try:
        rev_r = (supabase.table("sop_revisions")
                 .select("*")
                 .eq("sop_id", sop_id)
                 .order("revision_number", desc=True)
                 .execute())
        revisions = db_rows(rev_r)
    except Exception as e:
        logger.error("get_sop revisions fetch failed for %s: %s", sop_id, e)
        revisions = []
    return {"sop": _serialize(doc), "revisions": revisions}


# ── Create ────────────────────────────────────────────────────────────────────

@router.post("", dependencies=[Depends(require_role("manager"))])
@router.post("/", dependencies=[Depends(require_role("manager"))])
async def create_sop(body: SopCreate, current_user: dict = Depends(require_role("manager"))):
    payload = body.dict(exclude={"change_note"})
    payload["sections"] = body.sections.dict()
    payload["code"] = payload["code"].strip()
    now = datetime.utcnow().isoformat()
    payload["created_at"] = now
    payload["updated_at"] = now
    payload["created_by"] = current_user.get("email", "")
    payload["updated_by"] = current_user.get("email", "")

    for date_field in ("effective_date", "next_review_date"):
        if isinstance(payload.get(date_field), date):
            payload[date_field] = payload[date_field].isoformat()

    try:
        existing = (supabase.table("sop_documents")
                    .select("id")
                    .eq("code", payload["code"])
                    .execute())
        if db_rows(existing):
            raise HTTPException(409, f"An SOP with code '{payload['code']}' already exists")
        r = supabase.table("sop_documents").insert(payload).execute()
        created = one_row(r)
        if created is None:
            raise HTTPException(500, "Insert failed")
        _snapshot(created["id"], created, body.change_note, current_user)
        return _serialize(created)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_sop error: %s", e)
        raise HTTPException(500, "Could not create the SOP right now.")


# ── Update ────────────────────────────────────────────────────────────────────

@router.patch("/{sop_id}")
async def update_sop(
    sop_id: str, body: SopUpdate,
    authorization: Optional[str] = Header(None),
    current_user: dict = Depends(require_role("manager")),
):
    # Editing (incl. moving to draft/pilot/superseded/retired) needs manager+,
    # already enforced above. Making an SOP the effective operating standard
    # specifically needs admin+ — matching the governance manual's approval
    # matrix (a functional/executive approver is required for this class of
    # change) — a stricter bar than the route's own manager+ gate, checked the
    # same conditional way leaves.py/overtime.py check their own approve/reject
    # transitions.
    await require_role_if_status_in(body.status, {"effective"}, "admin", authorization, context="SOP approval")

    get_or_404(supabase, "sop_documents", sop_id, detail="SOP not found")

    payload = body.dict(exclude_unset=True, exclude={"change_note"})
    if "sections" in payload and payload["sections"] is not None:
        payload["sections"] = body.sections.dict()
    for date_field in ("effective_date", "next_review_date"):
        if isinstance(payload.get(date_field), date):
            payload[date_field] = payload[date_field].isoformat()
    payload["updated_at"] = datetime.utcnow().isoformat()
    payload["updated_by"] = current_user.get("email", "")

    try:
        r = supabase.table("sop_documents").update(payload).eq("id", sop_id).execute()
        updated = one_row(r)
        if updated is None:
            raise HTTPException(404, "SOP not found")
        _snapshot(sop_id, updated, body.change_note, current_user)
        return _serialize(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_sop error: %s", e)
        raise HTTPException(500, "Could not save the SOP right now.")


# ── Archive / Restore (soft-delete — no hard delete endpoint exists) ────────

@router.post("/{sop_id}/archive", dependencies=[Depends(require_role("manager"))])
async def archive_sop(sop_id: str, current_user: dict = Depends(require_role("manager"))):
    existing = get_or_404(supabase, "sop_documents", sop_id, detail="SOP not found")
    if existing.get("deleted_at"):
        raise HTTPException(400, "This SOP is already archived")
    try:
        r = (supabase.table("sop_documents")
             .update({"deleted_at": datetime.utcnow().isoformat(), "updated_by": current_user.get("email", "")})
             .eq("id", sop_id)
             .execute())
        updated = one_row(r)
        if updated is None:
            raise HTTPException(404, "SOP not found")
        return _serialize(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("archive_sop error: %s", e)
        raise HTTPException(500, "Could not archive the SOP right now.")


@router.post("/{sop_id}/restore", dependencies=[Depends(require_role("manager"))])
async def restore_sop(sop_id: str, current_user: dict = Depends(require_role("manager"))):
    existing = get_or_404(supabase, "sop_documents", sop_id, detail="SOP not found")
    if not existing.get("deleted_at"):
        raise HTTPException(400, "This SOP is not archived")
    try:
        r = (supabase.table("sop_documents")
             .update({"deleted_at": None, "updated_by": current_user.get("email", "")})
             .eq("id", sop_id)
             .execute())
        updated = one_row(r)
        if updated is None:
            raise HTTPException(404, "SOP not found")
        return _serialize(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("restore_sop error: %s", e)
        raise HTTPException(500, "Could not restore the SOP right now.")
