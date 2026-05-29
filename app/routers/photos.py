# backend/app/routers/photos.py — shared photo upload/delete for all pages
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query
from app.supabase_client import supabase
import uuid
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

BUCKET = "inspection-photos"

ALLOWED_EXTS = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "svg",
    "heic", "heif", "tiff", "tif", "avif", "ico",
    # raw formats some phones produce
    "raw", "dng", "cr2", "nef", "arw",
}

MAX_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post("/photos/upload")
async def upload_photo(
    file:   UploadFile = File(...),
    folder: str        = Form(default="misc"),
):
    """Upload an image to Supabase Storage and return its public URL."""
    original = file.filename or "photo.jpg"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else "jpg"

    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"Unsupported image format: .{ext}")

    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(400, "Photo too large — maximum 20 MB")

    storage_path = f"{folder}/{uuid.uuid4()}.{ext}"

    try:
        supabase.storage.from_(BUCKET).upload(
            storage_path,
            content,
            {"content-type": file.content_type or "image/jpeg"},
        )
    except Exception as e:
        logger.error(f"upload_photo storage error: {e}")
        raise HTTPException(500, f"Storage upload failed: {e}")

    try:
        url = supabase.storage.from_(BUCKET).get_public_url(storage_path)
    except Exception:
        url = ""

    logger.info(f"Photo uploaded: {storage_path}")
    return {"url": url, "path": storage_path}


@router.delete("/photos/delete")
async def delete_photo(path: str = Query(...)):
    """Remove a photo from Supabase Storage by its storage path."""
    try:
        supabase.storage.from_(BUCKET).remove([path])
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete_photo error: {e}")
        raise HTTPException(500, str(e))
