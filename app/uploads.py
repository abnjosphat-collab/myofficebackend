# backend/app/uploads.py — shared file-upload validation.
#
# Several upload endpoints across the routers accepted ANY file with no size
# cap and no type check at all (compressors.py's CSV import, spares.py's
# spreadsheet inference), or a size cap but no type check (documents.py,
# services.py's attachments) — each repeating its own ad hoc pattern instead
# of a shared one. This centralizes both checks so every upload endpoint
# gets the same treatment instead of whatever it happened to remember to add.
#
# photos.py and services.py's /ocr endpoint already validate both size and
# type well; they aren't touched here, just given the same shape for
# consistency where it was convenient.

from fastapi import HTTPException, UploadFile

from app.document_rules import content_type_for

# Purpose-scoped allowlists — extend per-endpoint as needed rather than
# accepting "anything", which is what several endpoints did before this.
DOCUMENT_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "txt",
    "jpg", "jpeg", "png", "gif", "webp", "zip",
}
SPREADSHEET_EXTS = {"csv", "tsv", "xls", "xlsx"}

# Noticeboard attachments cover a wider practical range than the Document Hub
# (safety briefing videos, voice-note announcements, scanned memos, OpenDocument
# files from non-MS-Office machines) — a real request, not "accept literally
# anything": still an explicit allowlist, still excludes executables/scripts
# (.exe, .sh, .bat, .msi, .ps1, .apk, etc.), just a broader one than DOCUMENT_EXTS.
NOTICE_ATTACHMENT_EXTS = DOCUMENT_EXTS | {
    "rtf", "odt", "ods", "odp", "svg", "bmp", "tif", "tiff", "heic",
    "rar", "7z", "tar", "gz",
    "mp3", "wav", "m4a", "ogg", "aac",
    "mp4", "mov", "avi", "webm", "mkv",
    "json", "xml",
}


def _ext(filename: str | None) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


# Body reads happen in these pieces so an oversize upload is refused after at
# most max_bytes + one chunk, instead of buffering the whole body first (a
# single large request could otherwise exhaust a small instance).
_READ_CHUNK_BYTES = 1024 * 1024


async def read_capped(file: UploadFile, *, max_bytes: int, too_large_detail: str) -> bytes:
    """Read an upload in chunks, refusing as soon as the cap is passed."""
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(_READ_CHUNK_BYTES)
        if not piece:
            break
        chunks.append(piece)
        total += len(piece)
        if total > max_bytes:
            raise HTTPException(status_code=400, detail=too_large_detail)
    return b"".join(chunks)


def storage_content_type(filename: str | None) -> str:
    """
    The content type to store with an upload: derived from the extension
    (documents.py's rule), never the client's header. SVG is stored as
    octet-stream — served as image/svg+xml from a public bucket it would run
    an embedded script in a colleague's browser; this keeps it downloadable
    but inert.
    """
    if _ext(filename) == "svg":
        return "application/octet-stream"
    return content_type_for(filename)


async def read_and_validate_upload(
    file: UploadFile,
    *,
    max_bytes: int,
    allowed_exts: set,
) -> bytes:
    """
    Reads an UploadFile's content, enforcing a size cap and an extension
    allowlist. Raises HTTPException(400) with a clear message on violation.
    Checks the extension BEFORE reading the body, so a disallowed file type
    doesn't get buffered into memory for nothing.
    """
    ext = _ext(file.filename)
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext or 'unknown'}")

    mb = max_bytes // (1024 * 1024)
    return await read_capped(file, max_bytes=max_bytes, too_large_detail=f"File too large — maximum {mb} MB")
