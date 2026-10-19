from typing import Optional
from pydantic import BaseModel
from app.crud_router import CrudRouter
from app.audit import actor_name


class FMCreate(BaseModel):
    equipment_type: str
    equipment_name: Optional[str] = None
    component: str
    failure_mode: str
    failure_cause: Optional[str] = None
    symptoms: Optional[str] = None
    detection_method: Optional[str] = None
    corrective_action: Optional[str] = None
    preventive_action: Optional[str] = None
    severity: Optional[int] = None
    probability: Optional[int] = None
    detectability: Optional[int] = None
    occurrence_count: int = 0
    last_occurred: Optional[str] = None
    # created_by is stamped by the server from the signed-in session, never taken from the browser.


class FMUpdate(BaseModel):
    equipment_type: Optional[str] = None
    equipment_name: Optional[str] = None
    component: Optional[str] = None
    failure_mode: Optional[str] = None
    failure_cause: Optional[str] = None
    symptoms: Optional[str] = None
    detection_method: Optional[str] = None
    corrective_action: Optional[str] = None
    preventive_action: Optional[str] = None
    severity: Optional[int] = None
    probability: Optional[int] = None
    detectability: Optional[int] = None
    occurrence_count: Optional[int] = None
    last_occurred: Optional[str] = None


def _stamp_create(payload: dict) -> dict:
    return {**payload, "created_by": actor_name()}


# Standard CRUD over failure_modes — see app/crud_router.py.
router = CrudRouter(
    "failure_modes", FMCreate, FMUpdate,
    tags=["Failure Modes"],
    order_by="equipment_type",
    filters={"equipment_type": "equipment_type"},
    not_found="Failure mode not found",
    before_create=_stamp_create,
).router
