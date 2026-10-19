# backend/app/routers/audit.py — read the record of who changed what (see app/audit.py for how it is written).
# Managers and above only. Reading is the only thing this router does; nothing here can change or erase the log.
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.audit import AUDITED_TABLES, audit_status
from app.auth import require_role
from app.errors import server_error
from app.supabase_client import supabase, rows

router = APIRouter()


@router.get("")
@router.get("/")
async def list_audit_entries(
    table: str = Query(..., description="The table, for example overtime or leaves"),
    record_id: Optional[str] = Query(None, description="Limit to one record"),
    limit: int = Query(100, ge=1, le=500),
    current_user: dict = Depends(require_role("manager")),
) -> List[Dict[str, Any]]:
    """Most recent first: when, who (name, email, role), what they did, and what changed."""
    if table not in AUDITED_TABLES:
        raise HTTPException(status_code=400, detail=f"'{table}' is not an audited table.")
    try:
        query = (
            supabase.table("audit_log")
            .select("id,at,actor_name,actor_email,actor_role,action,table_name,record_id,changes,snapshot")
            .eq("table_name", table)
        )
        if record_id:
            query = query.eq("record_id", record_id)
        return rows(query.order("at", desc=True).order("id", desc=True).limit(limit).execute())
    except Exception as exc:
        raise server_error(exc, "Loading the change history")


@router.get("/status")
async def audit_log_status(current_user: dict = Depends(require_role("manager"))) -> Dict[str, Any]:
    """Whether changes are being recorded right now (also shown on /api/health)."""
    return audit_status()
