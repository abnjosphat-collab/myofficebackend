from typing import Optional, List, Any
from fastapi import HTTPException, Depends
from pydantic import BaseModel
from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user
from app.db_helpers import get_or_404


class JobCardCreate(BaseModel):
    job_no: str
    title: str
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: str = "corrective"
    priority: str = "medium"
    status: str = "open"
    description: Optional[str] = None
    tasks: Optional[List[Any]] = []
    parts_used: Optional[List[Any]] = []
    labour_hours: Optional[float] = 0
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    notes: Optional[str] = None
    created_by: Optional[str] = None


class JobCardUpdate(BaseModel):
    title: Optional[str] = None
    equipment_id: Optional[str] = None
    equipment_name: Optional[str] = None
    type: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    tasks: Optional[List[Any]] = None
    parts_used: Optional[List[Any]] = None
    labour_hours: Optional[float] = None
    assigned_to: Optional[str] = None
    supervisor: Optional[str] = None
    section: Optional[str] = None
    scheduled_date: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    sign_off_by: Optional[str] = None
    sign_off_at: Optional[str] = None
    notes: Optional[str] = None


# Standard CRUD over job_cards (newest first) + a detail-view endpoint. reject_empty_update
# preserves the previous handler's 400 on a no-op PATCH.
router = CrudRouter(
    "job_cards", JobCardCreate, JobCardUpdate,
    tags=["Job Cards"],
    order_by="created_at", order_desc=True,
    filters={"status": "status", "priority": "priority", "section": "section"},
    not_found="Job card not found",
    reject_empty_update=True,
).router


@router.get("/{jc_id}", dependencies=[Depends(get_current_user)])
async def get_job_card(jc_id: int):
    return get_or_404(supabase, "job_cards", jc_id, detail="Job card not found")
