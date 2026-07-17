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

# Purpose-scoped allowlists — extend per-endpoint as needed rather than
# accepting "anything", which is what several endpoints did before this.
DOCUMENT_EXTS = {
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "txt",
    "jpg", "jpeg", "png", "gif", "webp", "zip",
}
SPREADSHEET_EXTS = {"csv", "tsv", "xls", "xlsx"}


def _ext(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


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

    content = await file.read()
    if len(content) > max_bytes:
        mb = max_bytes // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"File too large — maximum {mb} MB")

    return content
