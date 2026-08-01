# backend/app/routers/tasks_events.py — manager-only Events & Tasks board: upcoming
# events and to-do items in one list, tagged with task_type. Registered with a
# whole-router require_role("manager") dependency in main.py (same pattern as
# accounting) — every verb, including GET, is manager+ only.
from typing import Optional
from pydantic import BaseModel
from app.crud_router import CrudRouter


class TaskEventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "task"
    event_date: Optional[str] = None
    priority: str = "Medium"


class TaskEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    task_type: Optional[str] = None
    event_date: Optional[str] = None
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
