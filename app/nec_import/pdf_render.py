"""PDF page rendering for review UI (PyMuPDF)."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional


def render_page_png(pdf_path: Path, page_index: int, scale: float = 1.5) -> Optional[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    doc = fitz.open(pdf_path)
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return None
        page = doc.load_page(page_index)
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return base64.b64encode(pix.tobytes("png")).decode("ascii")
    finally:
        doc.close()
