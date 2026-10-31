# backend/app/routers/employee_import.py — Mining Excel importer (Batch 4).
#
# Admin only. The browser parses the workbook and sends normalised rows plus
# the column mapping; preview dry-runs (no writes), commit writes then logs
# to employee_imports, undo deletes what a commit created (through the normal
# delete path, so Recently deleted keeps them).
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.auth import require_role
from app.cache import invalidate_namespace
from app.employee_import import PlanError, commit_import, plan_import, undo_import
from app.errors import server_error
from app.supabase_client import supabase
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/employees/import", tags=["Employee Import"])


class ImportRequest(BaseModel):
    file_name: str = ""
    layout: Literal["wide", "long"] = "wide"
    department: str = "Mining"
    rows: list[list[Any]] = Field(default_factory=list)
    mapping: dict[str, Any] = Field(default_factory=dict)


def _actor(user: dict) -> str:
    return str(user.get("email") or user.get("user_id") or "")


@router.post("/preview")
async def preview_import(body: ImportRequest, current_user: dict = Depends(require_role("admin"))):
    """Dry-run: what would happen, without writing. Same validation as commit."""
    try:
        plan = await run_in_threadpool(
            lambda: plan_import(supabase, body.rows, body.mapping, body.layout, body.department))
    except PlanError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Import preview failed: {e}")
        raise server_error(e, "Previewing the import")
    return {"file_name": body.file_name, **plan}


@router.post("/commit")
async def commit_import_endpoint(body: ImportRequest,
                                current_user: dict = Depends(require_role("admin"))):
    """Write the import and log it to employee_imports. Re-plans on fresh
    reads, so a preview from an hour ago cannot write stale decisions."""
    try:
        result = await run_in_threadpool(
            lambda: commit_import(supabase, body.rows, body.mapping, body.layout,
                                  body.department, body.file_name, _actor(current_user)))
        await invalidate_namespace("employees")
    except PlanError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"Import commit failed: {e}")
        raise server_error(e, "Committing the import")
    return result


@router.post("/{import_id}/undo")
async def undo_import_endpoint(import_id: str, current_user: dict = Depends(require_role("admin"))):
    """Delete what the import created (people with newer records are kept and
    listed). Idempotent: an already-undone import succeeds with nothing to do."""
    try:
        result = await run_in_threadpool(lambda: undo_import(supabase, import_id))
        await invalidate_namespace("employees")
    except PlanError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 422, detail=str(e))
    except Exception as e:
        logger.error(f"Import undo failed: {e}")
        raise server_error(e, "Undoing the import")
    return result


@router.get("/history")
async def import_history(current_user: dict = Depends(require_role("admin"))):
    """The import log, newest first — with Undo beside each entry on the page."""
    try:
        resp = await run_in_threadpool(
            lambda: supabase.table("employee_imports").select("*")
            .order("created_at", desc=True).execute())
    except Exception as e:
        logger.error(f"Import history failed: {e}")
        raise server_error(e, "Loading import history")
    return list(resp.data or [])
