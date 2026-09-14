"""Pluggable scan extraction — credentials stay server-side."""
from __future__ import annotations

import os
from typing import Protocol

from app.nec_import.review_schema import validate_review_payload


class ExtractionProvider(Protocol):
    name: str

    def extract(self, job_id: str, period_start: str, period_end: str) -> dict:
        ...


class ReviewJsonUploadProvider:
    """Review JSON must already be uploaded to the job."""

    name = "review_json_upload"

    def extract(self, job_id: str, period_start: str, period_end: str) -> dict:
        from app.nec_import.job_store import load_review_json
        return load_review_json(job_id)


class ManualPendingProvider:
    """PDF stored; operator attaches validated review JSON (Sep 2026 workflow in-app)."""

    name = "manual_review_json"

    def extract(self, job_id: str, period_start: str, period_end: str) -> dict:
        raise RuntimeError(
            "Scan extraction is not configured. Upload a validated review JSON for this period "
            "(schema from NEC import preparation) or set NEC_IMPORT_EXTRACTION_PROVIDER when a "
            "vision provider is available."
        )


def get_extraction_provider() -> ExtractionProvider:
    key = (os.environ.get("NEC_IMPORT_EXTRACTION_PROVIDER") or "manual_review_json").strip().lower()
    if key in ("manual", "manual_review_json", "review_json"):
        return ManualPendingProvider()
    if key in ("review_json_upload", "uploaded"):
        return ReviewJsonUploadProvider()
    return ManualPendingProvider()


def run_extraction(job_id: str, period_start: str, period_end: str) -> dict:
    provider = get_extraction_provider()
    review = provider.extract(job_id, period_start, period_end)
    ok, errs = validate_review_payload(review)
    if not ok:
        raise ValueError("; ".join(errs))
    return review
