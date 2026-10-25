# backend/app/routers/recently_deleted.py — list what was deleted and put it back (see app/audit.py for how
# the copies are kept). Managers and above only. Restore re-inserts the exact row through the server, so every
# database rule still applies: a clash with a live row, or a row that no longer fits, comes back as the normal
# 409/422 message instead of a silent overwrite. Documents are refused here — they keep their own trash.
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.audit import TRASH_EXCLUDED, TRASH_TABLE
from app.auth import require_role
from app.errors import server_error
from app.supabase_client import one_row, rows, supabase

router = APIRouter()

# PostgREST operator characters: stripped from the search box so a typed comma or
# bracket filters literally instead of reshaping the query.
_SEARCH_STRIP = re.compile(r"[,()%.]")


def _search_or(term: str) -> str:
    cleaned = _SEARCH_STRIP.sub("", term).strip()
    return ",".join(
        f"{col}.ilike.*{cleaned}*" for col in ("table_name", "record_id", "deleted_by_email", "deleted_by_name")
    )


def _id_columns(snapshot: Dict[str, Any]) -> List[str]:
    return [col for col in ("id", "record_id") if snapshot.get(col) is not None]


@router.get("")
@router.get("/")
async def list_recently_deleted(
    q: Optional[str] = Query(None, description="Search table, record id or who deleted it"),
    table: Optional[str] = Query(None, description="Limit to one table"),
    include_restored: bool = Query(False, description="Also list copies that were already put back"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_role("manager")),
) -> List[Dict[str, Any]]:
    """Most recently deleted first. Copies are kept forever; restoring one marks it, never removes it."""
    try:
        query = supabase.table(TRASH_TABLE).select("*")
        if not include_restored:
            query = query.is_("restored_at", "null")
        if table:
            query = query.eq("table_name", table)
        if q and q.strip():
            query = query.or_(_search_or(q))
        return rows(query.order("deleted_at", desc=True).order("id", desc=True).limit(limit).offset(offset).execute())
    except Exception as exc:
        raise server_error(exc, "Loading recently deleted records")


@router.post("/{entry_id}/restore")
async def restore_deleted(
    entry_id: str,
    current_user: dict = Depends(require_role("manager")),
) -> Dict[str, Any]:
    """Put one deleted record back exactly as it was."""
    try:
        entry = one_row(
            supabase.table(TRASH_TABLE).select("*").eq("id", entry_id).limit(1).execute()
        )
    except Exception as exc:
        raise server_error(exc, "Loading the deleted record")
    if entry is None:
        raise HTTPException(status_code=404, detail="That record is not in Recently deleted.")
    if entry.get("restored_at"):
        raise HTTPException(status_code=409, detail="That record was already put back.")
    table = entry.get("table_name") or ""
    snapshot = entry.get("snapshot")
    if table in TRASH_EXCLUDED or table == TRASH_TABLE:
        raise HTTPException(status_code=400, detail="That table restores through its own trash, not here.")
    if not isinstance(snapshot, dict) or not snapshot:
        raise HTTPException(status_code=400, detail="That copy has no record left to put back.")
    try:
        for column in _id_columns(snapshot):
            clash = one_row(
                supabase.table(table).select(column).eq(column, snapshot[column]).limit(1).execute()
            )
            if clash is not None:
                raise HTTPException(
                    status_code=409,
                    detail="A live record with this ID already exists. It was probably re-created after the delete.",
                )
        rows(supabase.table(table).insert(snapshot).execute())
    except HTTPException:
        raise
    except Exception as exc:
        raise server_error(exc, f"Restoring the {table} record")
    actor = current_user or {}
    try:
        rows(
            supabase.table(TRASH_TABLE)
            .update({
                "restored_at": datetime.now(timezone.utc).isoformat(),
                "restored_by": actor.get("email") or actor.get("name") or "system",
            })
            .eq("id", entry_id)
            .execute()
        )
    except Exception as exc:
        raise server_error(exc, "Marking the record as put back")
    return {"success": True, "table_name": table, "record_id": entry.get("record_id")}
