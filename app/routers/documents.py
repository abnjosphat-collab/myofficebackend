# app/routers/documents.py — AMS Document Hub (CRUD + Supabase Storage)
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.uploads import read_and_validate_upload, DOCUMENT_EXTS
import logging, uuid as uuid_module

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

BUCKET = "ams-documents"


def _file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"jpg", "jpeg", "png", "gif", "bmp", "webp", "svg"}: return "image"
    if ext in {"mp4", "avi", "mov", "wmv", "mkv", "webm"}:         return "video"
    if ext in {"mp3", "wav", "ogg", "m4a", "flac"}:                return "audio"
    if ext in {"doc", "docx", "txt", "md", "rtf"}:                 return "document"
    if ext in {"xls", "xlsx", "csv"}:                              return "spreadsheet"
    if ext == "pdf":                                                return "pdf"
    if ext in {"zip", "rar", "7z", "tar", "gz"}:                   return "archive"
    return "file"


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def list_documents(category_id: str, folder_id: Optional[str] = None):
    try:
        q = supabase.table("documents").select("*").eq("category_id", category_id)
        if folder_id:
            q = q.eq("folder_id", folder_id)
        else:
            q = q.is_("folder_id", "null")
        r = q.order("created_at", desc=True).execute()
        return r.data or []
    except Exception as e:
        logger.error("list_documents error: %s", e)
        raise HTTPException(500, str(e))


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload_document(
    file: UploadFile  = File(...),
    name: str         = Form(""),
    description: str  = Form(""),
    category_id: str  = Form(""),
    category_name: str= Form(""),
    folder_id: str    = Form(""),   # empty string = root
    folder_path: str  = Form(""),
    current_user: dict = Depends(get_current_user),
):
    content = await read_and_validate_upload(file, max_bytes=100 * 1024 * 1024, allowed_exts=DOCUMENT_EXTS)

    original_name = file.filename or "upload"
    display_name  = name.strip() or original_name
    ext           = original_name.rsplit(".", 1)[-1] if "." in original_name else "bin"
    real_folder   = folder_id.strip() or None
    storage_path  = f"{category_id}/{real_folder or 'root'}/{uuid_module.uuid4()}.{ext}"

    try:
        supabase.storage.from_(BUCKET).upload(
            storage_path,
            content,
            {"content-type": file.content_type or "application/octet-stream"},
        )
    except Exception as e:
        raise HTTPException(500, f"Storage upload failed: {e}")

    try:
        file_url = supabase.storage.from_(BUCKET).get_public_url(storage_path)
    except Exception:
        file_url = ""

    now = datetime.utcnow().isoformat()
    row = {
        "name":          display_name,
        "original_name": original_name,
        "storage_path":  storage_path,
        "file_url":      file_url,
        "file_size":     len(content),
        "mime_type":     file.content_type or "application/octet-stream",
        "file_type":     _file_type(original_name),
        "category_id":   category_id,
        "category_name": category_name,
        "folder_id":     real_folder,
        "folder_path":   folder_path,
        "description":   description.strip(),
        "starred":       False,
        "created_at":    now,
        "updated_at":    now,
    }
    try:
        r = supabase.table("documents").insert(row).execute()
        return r.data[0]
    except Exception as e:
        logger.error("documents insert error: %s", e)
        raise HTTPException(500, str(e))


# ── Update (rename / star / comment) ─────────────────────────────────────────

class DocUpdate(BaseModel):
    name:        Optional[str]  = None
    description: Optional[str] = None
    starred:     Optional[bool] = None


@router.put("/{doc_id}")
async def update_document(doc_id: str, body: DocUpdate, current_user: dict = Depends(get_current_user)):
    updates: dict = {"updated_at": datetime.utcnow().isoformat()}
    if body.name        is not None: updates["name"]        = body.name
    if body.description is not None: updates["description"] = body.description
    if body.starred     is not None: updates["starred"]     = body.starred
    try:
        r = supabase.table("documents").update(updates).eq("id", doc_id).execute()
        if not r.data:
            raise HTTPException(404, "Document not found")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_document error: %s", e)
        raise HTTPException(500, str(e))


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/{doc_id}")
async def delete_document(doc_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        row = (supabase.table("documents")
               .select("storage_path")
               .eq("id", doc_id)
               .maybe_single()
               .execute())
        if row.data and row.data.get("storage_path"):
            try:
                supabase.storage.from_(BUCKET).remove([row.data["storage_path"]])
            except Exception as e:
                logger.warning("Storage delete failed (continuing): %s", e)
        supabase.table("documents").delete().eq("id", doc_id).execute()
        return {"ok": True}
    except Exception as e:
        logger.error("delete_document error: %s", e)
        raise HTTPException(500, str(e))
