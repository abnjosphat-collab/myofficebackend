from typing import Optional, List, Any
from fastapi import HTTPException, Depends
from pydantic import BaseModel
from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user
from app.db_helpers import get_or_404
from app.scope import assert_row_visible


class HandoverCreate(BaseModel):
    handover_date: str
    shift: str
    outgoing_supervisor: str
    incoming_supervisor: Optional[str] = None
    section: Optional[str] = None
    equipment_summary: Optional[List[Any]] = []
    completed_work: Optional[str] = None
    outstanding_work: Optional[str] = None
    safety_concerns: Optional[str] = None
    environmental_issues: Optional[str] = None
    production_notes: Optional[str] = None
    general_notes: Optional[str] = None


class HandoverUpdate(BaseModel):
    incoming_supervisor: Optional[str] = None
    section: Optional[str] = None
    equipment_summary: Optional[List[Any]] = None
    completed_work: Optional[str] = None
    outstanding_work: Optional[str] = None
    safety_concerns: Optional[str] = None
    environmental_issues: Optional[str] = None
    production_notes: Optional[str] = None
    general_notes: Optional[str] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[str] = None


# Standard CRUD over shift_handovers (newest first) + a detail-view endpoint.
router = CrudRouter(
    "shift_handovers", HandoverCreate, HandoverUpdate,
    tags=["Shift Handover"],
    order_by="created_at", order_desc=True,
    filters={"shift": "shift", "section": "section"},
    not_found="Handover not found",
    scope_table="shift_handovers",
).router


@router.get("/{h_id}", dependencies=[Depends(get_current_user)])
async def get_handover(h_id: int, current_user: dict = Depends(get_current_user)):
    row = get_or_404(supabase, "shift_handovers", h_id, detail="Handover not found")
    return assert_row_visible(current_user, "shift_handovers", row, detail="Handover not found")
