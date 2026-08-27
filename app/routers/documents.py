# app/routers/documents.py — AMS Document Hub (CRUD + Supabase Storage)
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.uploads import read_and_validate_upload, DOCUMENT_EXTS
from app.db_helpers import or_ilike
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


# ── Global search (across every category/folder) ─────────────────────────────

@router.get("/search", dependencies=[Depends(get_current_user)])
async def search_documents(q: str):
    if not q or not q.strip():
        return []
    try:
        r = (supabase.table("documents")
             .select("*")
             .or_(or_ilike(["name", "description", "original_name"], q))
             .order("created_at", desc=True)
             .limit(100)
             .execute())
        return r.data or []
    except Exception as e:
        logger.error("search_documents error: %s", e)
        raise HTTPException(500, str(e))


# ── Folders (custom subfolders within a category) ────────────────────────────

class FolderCreate(BaseModel):
    category_id: str
    category_name: str = ""
    name: str


class FolderUpdate(BaseModel):
    name: str


@router.get("/folders", dependencies=[Depends(get_current_user)])
async def list_folders(category_id: str):
    try:
        r = (supabase.table("document_folders")
             .select("*")
             .eq("category_id", category_id)
             .order("name")
             .execute())
        return r.data or []
    except Exception as e:
        logger.error("list_folders error: %s", e)
        raise HTTPException(500, str(e))


@router.post("/folders")
async def create_folder(body: FolderCreate, current_user: dict = Depends(get_current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Folder name is required")
    try:
        existing = (supabase.table("document_folders")
                    .select("id")
                    .eq("category_id", body.category_id)
                    .eq("name", name)
                    .maybe_single()
                    .execute())
        if existing.data:
            raise HTTPException(409, "A folder with this name already exists")
        r = supabase.table("document_folders").insert({
            "category_id":   body.category_id,
            "category_name": body.category_name,
            "name":          name,
        }).execute()
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_folder error: %s", e)
        raise HTTPException(500, str(e))


@router.put("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderUpdate, current_user: dict = Depends(get_current_user)):
    # Documents tag their folder by *name* (folder_id/folder_path are free-text
    # fields, not a foreign key to this table — see upload_document above), so a
    # rename has to cascade onto every document currently tagged with the old
    # name or they'd silently vanish from the folder's file list.
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Folder name is required")
    try:
        existing = (supabase.table("document_folders")
                    .select("*")
                    .eq("id", folder_id)
                    .maybe_single()
                    .execute())
        if not existing.data:
            raise HTTPException(404, "Folder not found")
        old_name = existing.data["name"]
        category_id = existing.data["category_id"]

        r = (supabase.table("document_folders")
             .update({"name": name})
             .eq("id", folder_id)
             .execute())

        if old_name != name:
            (supabase.table("documents")
             .update({"folder_id": name, "folder_path": name})
             .eq("category_id", category_id)
             .eq("folder_id", old_name)
             .execute())

        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("rename_folder error: %s", e)
        raise HTTPException(500, str(e))


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        supabase.table("document_folders").delete().eq("id", folder_id).execute()
        return {"ok": True}
    except Exception as e:
        logger.error("delete_folder error: %s", e)
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
    except Exception as e:
        # The file itself is already safely uploaded and verified above (storage_path
        # is saved below either way) — only the display URL failed to generate. Log it
        # so it's not invisible, but don't fail an otherwise-good upload over it; the
        # frontend shows "link unavailable" rather than a dead link for file_url="".
        logger.error("get_public_url failed for %s: %s", storage_path, e)
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
