# app/routers/notices.py
"""
Notices Router — noticeboard announcements

Migrated onto the shared CrudRouter (see app/crud_router.py) — list/create/update/
delete was plain CRUD once the id-type gap was closed (notices uses a string/UUID
id, not a SERIAL int; CrudRouter didn't support that until this migration added
id_type=str — a real, reusable gap, not a one-off hack: several other tables in
this codebase are also string-keyed).

NoticeUpdate is now genuinely all-Optional (was a NoticeCreate subclass — every
field required, matching the original PUT-only endpoint). Found while migrating:
useNoticeboardData.ts's togglePin() already sends a PATCH with only
`{is_pinned}` — there was never a PATCH route for it to hit (only PUT existed),
so the pin button has been live-broken (toast.error('Failed to toggle pin') on
every click) until this migration adds a real partial-update PATCH endpoint.
updateNotice() (the full edit form) still sends every field either way, so it's
unaffected by the model becoming all-Optional.

/{notice_id} (single-record GET) and /stats/summary are genuinely custom (no
generic-base equivalent for the former; the latter is an aggregation) and stay
hand-added alongside the base, same pattern as contractors.py.
"""
from fastapi import HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date as DateType
from app.supabase_client import supabase, rows
from app.auth import get_current_user
from app.aggregation import count_by
from app.db_helpers import get_or_404
from app.crud_router import CrudRouter
from app.uploads import read_and_validate_upload, NOTICE_ATTACHMENT_EXTS
import logging, uuid as uuid_module

logger = logging.getLogger(__name__)

# Reuses the Document Hub's storage bucket (blob storage, not a table — no reason to
# provision a second bucket) under its own "notices/" prefix, but deliberately does
# NOT write a row into the `documents` table: a notice's attachment is a property of
# the notice, not a Document Hub entry, and inserting one there would (a) mix two
# unrelated concerns in one table and (b) need a category_id that doesn't correspond
# to any real Document Hub category.
ATTACHMENT_BUCKET = "ams-documents"

# One file: {name, url, size}. `attachments` replaced the old singular
# attachment_name/attachment_url/attachment_size columns (see
# supabase_migration_notices_multi_attachment.sql) so a notice can carry as many
# files as needed instead of exactly one.
class Attachment(BaseModel):
    name: str
    url: str
    size: str

# Pydantic Model - matches SQL exactly
class NoticeCreate(BaseModel):
    title: str
    content: str
    date: DateType
    category: str = "General"
    priority: str = "Medium"
    status: str = "Draft"
    is_pinned: bool = False
    requires_acknowledgment: bool = False
    author: Optional[str] = None
    department: Optional[str] = "General"
    expires_at: Optional[DateType] = None
    target_audience: Optional[str] = "All Employees"
    notification_type: Optional[str] = "General Announcement"
    attachments: List[Attachment] = []

class NoticeUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    date: Optional[DateType] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    is_pinned: Optional[bool] = None
    requires_acknowledgment: Optional[bool] = None
    author: Optional[str] = None
    department: Optional[str] = None
    expires_at: Optional[DateType] = None
    target_audience: Optional[str] = None
    notification_type: Optional[str] = None
    attachments: Optional[List[Attachment]] = None


def _dates_to_iso(data: dict) -> dict:
    """date/expires_at arrive as Python date objects (Pydantic-parsed) — Supabase
    needs ISO strings. Mirrors the original hand-written router's conversion."""
    if isinstance(data.get('date'), DateType):
        data['date'] = data['date'].isoformat()
    if isinstance(data.get('expires_at'), DateType):
        data['expires_at'] = data['expires_at'].isoformat()
    return data


router = CrudRouter(
    "notices", NoticeCreate, NoticeUpdate,
    order_by="date", order_desc=True,
    # is_pinned added — it was in the frontend's filter UI (useNoticeboardData.ts
    # already sent it as a query param) but never registered here, so "Pinned Only" /
    # "Not Pinned" silently did nothing: the backend returned every notice regardless.
    filters={"category": "category", "priority": "priority", "status": "status", "department": "department", "is_pinned": "is_pinned"},
    search_columns=["title", "content"],
    not_found="Notice not found",
    before_create=_dates_to_iso,
    before_update=_dates_to_iso,
    id_type=str,
).router


# Upload a notice attachment — separate from documents.py's /api/documents/upload
# (see ATTACHMENT_BUCKET's comment above for why). The frontend previously read the
# picked File's name/size into the form and never uploaded it anywhere, so
# attachment_url stayed blank and "Download attachment" had nothing to link to —
# this is the endpoint that actually makes that work.
@router.post("/upload-attachment", dependencies=[Depends(get_current_user)])
async def upload_notice_attachment(file: UploadFile = File(...)):
    content = await read_and_validate_upload(file, max_bytes=100 * 1024 * 1024, allowed_exts=NOTICE_ATTACHMENT_EXTS)

    original_name = file.filename or "attachment"
    ext = original_name.rsplit(".", 1)[-1] if "." in original_name else "bin"
    storage_path = f"notices/{uuid_module.uuid4()}.{ext}"

    try:
        supabase.storage.from_(ATTACHMENT_BUCKET).upload(
            storage_path,
            content,
            {"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {e}")

    try:
        file_url = supabase.storage.from_(ATTACHMENT_BUCKET).get_public_url(storage_path)
    except Exception as e:
        # Matches documents.py's own upload_document: the file is already safely
        # uploaded above, only the display URL failed to generate — log it rather
        # than fail an otherwise-good upload, and let the frontend show "link
        # unavailable" instead of a dead link.
        logger.error("get_public_url failed for %s: %s", storage_path, e)
        file_url = ""

    size_mb = len(content) / (1024 * 1024)
    return {
        "name": original_name,
        "url": file_url,
        "size": f"{size_mb:.2f} MB",
    }


# GET single notice — no generic-base equivalent (CrudRouter has no get-by-id).
@router.get("/{notice_id}", dependencies=[Depends(get_current_user)])
async def get_notice(notice_id: str):
    try:
        return get_or_404(supabase, "notices", notice_id, detail="Notice not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# GET statistics
@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        response = supabase.table("notices").select("*").execute()
        notices = rows(response)

        stats = {
            "total_notices": len(notices),
            "status_breakdown": count_by(notices, 'status', default='Draft'),
            "priority_breakdown": count_by(notices, 'priority', default='Medium'),
            "category_breakdown": count_by(notices, 'category', default='General'),
            "pinned_count": sum(1 for n in notices if n.get('is_pinned')),
            "expired_count": 0,
            "expiring_soon_count": 0
        }

        for notice in notices:
            # Expiry calculations
            expires_at = notice.get('expires_at')
            if expires_at:
                try:
                    expiry_date = datetime.fromisoformat(expires_at.replace('Z', '+00:00')).date()
                    today = datetime.now().date()

                    if expiry_date < today:
                        stats["expired_count"] += 1
                    else:
                        days = (expiry_date - today).days
                        if 0 <= days <= 7:
                            stats["expiring_soon_count"] += 1
                except Exception:
                    pass

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
