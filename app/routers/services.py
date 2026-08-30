# app/routers/services.py — Services Tracker (CRUD + OCR + Attachments)
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.uploads import read_and_validate_upload, DOCUMENT_EXTS

# Alias prevents the Pydantic field named 'date' from shadowing datetime.date in type resolution
_Date = date
import logging, io, re
import uuid as uuid_module

# Lazy-loaded easyocr reader — cached after first use (model download happens once)
_ocr_reader = None

def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr  # pyright: ignore[reportMissingImports] — deliberately absent from .venv (drags in PyTorch); see backend README
        _ocr_reader = easyocr.Reader(['en'], gpu=False, verbose=False)
    return _ocr_reader

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/services", tags=["services"])

# ── Pydantic models ────────────────────────────────────────────────────────────

class ServiceIn(BaseModel):
    date: Optional[_Date] = None
    description: str = ''
    supplier: str = ''
    contact_person: str = ''
    requisition_number: str = ''
    invoice_number: str = ''
    order_number: str = ''
    amount: str = ''
    category: str = ''
    general_comments: str = ''
    # Stage 1: Planning
    planning_signed: bool = False
    planning_signed_by: str = ''
    planning_signed_date: Optional[date] = None
    planning_comments: str = ''
    # Stage 2: Engineering Manager
    eng_mgr_signed: bool = False
    eng_mgr_signed_by: str = ''
    eng_mgr_signed_date: Optional[date] = None
    eng_mgr_comments: str = ''
    # Stage 3: Finance
    finance_signed: bool = False
    finance_signed_by: str = ''
    finance_signed_date: Optional[date] = None
    finance_comments: str = ''
    # Stage 4: General Manager
    gm_signed: bool = False
    gm_signed_by: str = ''
    gm_signed_date: Optional[date] = None
    gm_comments: str = ''
    # Stage 5: Stores / GRV
    stores_signed: bool = False
    stores_signed_by: str = ''
    stores_signed_date: Optional[date] = None
    stores_comments: str = ''
    stores_grv_number: str = ''
    # Stage 6: Payment
    payment_done: bool = False
    payment_paid_by: str = ''
    payment_date: Optional[date] = None
    payment_reference: str = ''
    payment_comments: str = ''


def _serialize(data: dict) -> dict:
    """Convert date objects to ISO strings for Supabase."""
    return {
        k: v.isoformat() if isinstance(v, date) else v
        for k, v in data.items()
    }


# ── CRUD ───────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def list_services():
    try:
        r = supabase.table("services").select("*").order("created_at", desc=True).execute()
        return r.data or []
    except Exception as e:
        logger.error(f"list_services error: {e}")
        raise HTTPException(500, str(e))


@router.post("")
@router.post("/")
async def create_service(body: ServiceIn, current_user: dict = Depends(get_current_user)):
    try:
        data = _serialize(body.model_dump())
        now = datetime.utcnow().isoformat()
        data['created_at'] = now
        data['updated_at'] = now
        r = supabase.table("services").insert(data).execute()
        return r.data[0]
    except Exception as e:
        logger.error(f"create_service error: {e}")
        raise HTTPException(500, str(e))


# ── OCR helpers ───────────────────────────────────────────────────────────────

def _parse_service_text(text: str) -> dict:
    """Regex-based field extraction from raw OCR text.

    The label/value separator is `[ \\t:]+`, NOT `\\s` (which also matches
    newline) — found via testing: a document header/title containing a field's
    label word standalone (e.g. "SERVICE INVOICE" as a title, followed by
    "Date: ..." on the next line) matched the invoice_number pattern's
    `\\s*` as "label, then a newline as the required separator", capturing the
    NEXT LINE'S unrelated leading token ("Date:") as the invoice number. Every
    field here shares this same separator, so all of them had this bug, not
    just invoice_number. Restricting to same-line whitespace fixes it; OCR
    output realistically always has "Label: value" on one line for the
    layouts this parser targets, so same-line-only is the correct default.
    """

    def find(pattern: str, flags: int = re.I) -> Optional[str]:
        m = re.search(pattern, text, flags)
        return m.group(1).strip() if m else None

    # Date — accept DD/MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, etc.
    date_val = None
    dm = re.search(
        r'\b(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})\b',
        text,
    )
    if dm:
        try:
            from dateutil import parser as dtp
            date_val = dtp.parse(dm.group(1), dayfirst=True).strftime("%Y-%m-%d")
        except Exception:
            date_val = dm.group(1)

    return {
        "date": date_val,
        "description": find(
            r'(?:description|service\s*desc(?:ription)?|work\s*desc(?:ription)?|scope\s*of\s*work)[ \t:]+([^\n]{5,})'
        ),
        "supplier": find(
            r'(?:supplier|vendor|from|company|contractor|service\s*provider)[ \t:]+([^\n]+)'
        ),
        "contact_person": find(
            r'(?:contact\s*(?:person)?|attention|attn\b|representative|rep\b)[ \t:]+([^\n]+)'
        ),
        "requisition_number": find(
            r'(?:requisition\s*(?:no|number|#|nr)?\.?|req\.?\s*(?:no|#|nr)?)[ \t:]+([^\s\n,;]+)'
        ),
        "invoice_number": find(
            r'(?:invoice\s*(?:no|number|#|nr)?\.?|inv\.?\s*(?:no|#|nr)?)[ \t:]+([^\s\n,;]+)'
        ),
        "order_number": find(
            r'(?:(?:purchase\s*)?order\s*(?:no|number|#|nr)?\.?|p\.?\s*o\.?\s*(?:no|#|nr)?)[ \t:]+([^\s\n,;]+)'
        ),
        "amount": find(
            r'(?:amount|total|value|cost|price)[ \t:]+([R$€£]\s*[\d\s,]+\.?\d*|[\d\s,]+\.?\d*\s*(?:ZAR|USD|EUR|GBP))'
        ),
        "category": find(
            r'(?:category|type\s*of\s*service|service\s*type|trade)[ \t:]+([^\n]+)'
        ),
        "grv_number": find(
            r'(?:GRV\s*(?:no|number|#|nr)?\.?)[ \t:]+([^\s\n,;]+)'
        ),
        "payment_reference": find(
            r'(?:payment\s*ref(?:erence)?\.?|pay(?:ment)?\s*(?:no|ref|#)|transaction\s*(?:no|ref|#))[ \t:]+([^\s\n,;]+)'
        ),
        "general_comments": find(
            r'(?:comment|note|remark)s?[ \t:]+([^\n]+)'
        ),
    }


# ── OCR — must be registered BEFORE /{service_id} routes ──────────────────────

@router.post("/ocr")
async def ocr_document(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    """Extract service completion fields from a PDF or image using PyMuPDF + pytesseract (no AI API)."""
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "File too large — maximum 20 MB")

    mime = (file.content_type or "").lower().split(";")[0].strip()
    supported = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/tiff", "image/gif"}
    if mime not in supported:
        raise HTTPException(400, f"Unsupported type '{mime}'. Use PDF, JPEG, PNG, TIFF, or WEBP.")

    try:
        import fitz           # PyMuPDF
        from PIL import Image  # Pillow
        import numpy as np
    except ImportError as exc:
        raise HTTPException(503, f"OCR library not available: {exc}. Run: pip install pymupdf easyocr Pillow")

    try:
        text_parts: list[str] = []

        if mime == "application/pdf":
            doc = fitz.open(stream=content, filetype="pdf")
            for page in doc:
                page_text = page.get_text("text")
                if page_text.strip():
                    # Digital PDF — text already embedded, no OCR needed
                    text_parts.append(page_text)
                else:
                    # Scanned page — render to image then run easyocr
                    pix = page.get_pixmap(dpi=200)
                    img_arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                    results = _get_ocr_reader().readtext(img_arr, detail=0)
                    text_parts.append("\n".join(results))
            doc.close()
        else:
            img = Image.open(io.BytesIO(content)).convert("RGB")
            img_arr = np.array(img)
            results = _get_ocr_reader().readtext(img_arr, detail=0)
            text_parts.append("\n".join(results))

        full_text = "\n".join(text_parts)
        logger.debug("OCR raw text:\n%s", full_text[:500])
        return _parse_service_text(full_text)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("OCR error: %s", exc)
        raise HTTPException(502, f"OCR extraction failed: {exc}")


# ── Single-record operations ───────────────────────────────────────────────────

@router.put("/{service_id}")
async def update_service(service_id: str, body: ServiceIn, current_user: dict = Depends(get_current_user)):
    try:
        data = _serialize(body.model_dump(exclude_none=False))
        data['updated_at'] = datetime.utcnow().isoformat()
        r = supabase.table("services").update(data).eq("id", service_id).execute()
        if not r.data:
            raise HTTPException(404, "Service record not found")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_service error: {e}")
        raise HTTPException(500, str(e))


@router.delete("/{service_id}")
async def delete_service(service_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        supabase.table("services").delete().eq("id", service_id).execute()
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete_service error: {e}")
        raise HTTPException(500, str(e))


# ── Attachments ────────────────────────────────────────────────────────────────

BUCKET = "service-attachments"


@router.get("/{service_id}/attachments", dependencies=[Depends(get_current_user)])
async def list_attachments(service_id: str):
    try:
        r = (supabase.table("service_attachments")
             .select("*")
             .eq("service_id", service_id)
             .order("created_at", desc=True)
             .execute())
        return r.data or []
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/{service_id}/attachments")
async def upload_attachment(service_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    content = await read_and_validate_upload(file, max_bytes=50 * 1024 * 1024, allowed_exts=DOCUMENT_EXTS)

    original_name = file.filename or "upload"
    ext = original_name.rsplit(".", 1)[-1] if "." in original_name else "bin"
    storage_path = f"{service_id}/{uuid_module.uuid4()}.{ext}"

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

    meta = {
        "service_id": service_id,
        "filename": original_name,
        "file_url": file_url,
        "storage_path": storage_path,
        "file_size": len(content),
        "mime_type": file.content_type or "application/octet-stream",
        "created_at": datetime.utcnow().isoformat(),
    }
    r = supabase.table("service_attachments").insert(meta).execute()
    return r.data[0]


@router.delete("/{service_id}/attachments/{attachment_id}")
async def delete_attachment(service_id: str, attachment_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        row = (supabase.table("service_attachments")
               .select("storage_path")
               .eq("id", attachment_id)
               .maybe_single()
               .execute())
        if row.data and row.data.get("storage_path"):
            try:
                supabase.storage.from_(BUCKET).remove([row.data["storage_path"]])
            except Exception as e:
                logger.warning(f"Storage delete failed (continuing): {e}")
        supabase.table("service_attachments").delete().eq("id", attachment_id).execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, str(e))
