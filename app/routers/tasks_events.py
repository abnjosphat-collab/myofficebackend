# backend/app/routers/tasks_events.py — manager-only Events & Tasks board: upcoming
# events and to-do items in one list, tagged with task_type. Registered with a
# whole-router require_role("manager") dependency in main.py (same pattern as
# accounting) — every verb, including GET, is manager+ only.
from typing import List, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from app.crud_router import CrudRouter
from app.supabase_client import supabase
from app.auth import get_current_user


class TaskEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "task"
    event_date: Optional[str] = None
    due_date: Optional[str] = None
    responsible_people: Optional[List[str]] = None
    priority: str = "Medium"


class TaskEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    event_date: Optional[str] = None
    due_date: Optional[str] = None
    responsible_people: Optional[List[str]] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    completed_by: Optional[str] = None
    completed_at: Optional[str] = None


router = CrudRouter(
    "tasks_events", TaskEventCreate, TaskEventUpdate,
    tags=["Tasks & Events"],
    order_by="event_date",
    filters={"status": "status", "task_type": "task_type"},
    not_found="Task/event not found",
).router


# ─── Progress comments ──────────────────────────────────────────────────────
# An append-only log, not a full editable thread — no PATCH/DELETE on purpose.
# Same "extra endpoints bolted onto the CrudRouter-returned router" technique
# as contractors.py's /jobs sub-resource; this table doesn't fit the generic
# CRUD shape (it's scoped under a parent task_id), so it's hand-written here.

class TaskEventComment(BaseModel):
    author: Optional[str] = None
    text: str


@router.get("/{task_id}/comments", dependencies=[Depends(get_current_user)])
async def list_comments(task_id: int):
    r = (
        supabase.table("tasks_events_comments")
        .select("*")
        .eq("task_id", task_id)
        .order("created_at")
        .execute()
    )
    return r.data or []


@router.post("/{task_id}/comments", dependencies=[Depends(get_current_user)])
async def add_comment(task_id: int, data: TaskEventComment):
    r = (
        supabase.table("tasks_events_comments")
        .insert({**data.dict(), "task_id": task_id})
        .execute()
    )
    if not r.data:
        raise HTTPException(status_code=500, detail="Insert failed")
    return r.data[0]
