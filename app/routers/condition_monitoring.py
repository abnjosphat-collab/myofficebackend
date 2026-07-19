from typing import Optional
from pydantic import BaseModel
from app.crud_router import CrudRouter


class CMCreate(BaseModel):
    equipment_id: Optional[str] = None
    equipment_name: str
    component: Optional[str] = None
    monitoring_type: str
    sampled_date: str
    value: Optional[float] = None
    unit: Optional[str] = None
    iron_ppm: Optional[float] = None
    copper_ppm: Optional[float] = None
    lead_ppm: Optional[float] = None
    viscosity: Optional[float] = None
    water_pct: Optional[float] = None
    result: str = "normal"
    lab_reference: Optional[str] = None
    technician: Optional[str] = None
    notes: Optional[str] = None


class CMUpdate(BaseModel):
    equipment_name: Optional[str] = None
    component: Optional[str] = None
    monitoring_type: Optional[str] = None
    sampled_date: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    iron_ppm: Optional[float] = None
    copper_ppm: Optional[float] = None
    lead_ppm: Optional[float] = None
    viscosity: Optional[float] = None
    water_pct: Optional[float] = None
    result: Optional[str] = None
    lab_reference: Optional[str] = None
    technician: Optional[str] = None
    notes: Optional[str] = None


# Standard CRUD over condition_monitoring — see app/crud_router.py. Ordered by most
# recent sample first (sampled_date desc), matching the previous hand-written handler.
router = CrudRouter(
    "condition_monitoring", CMCreate, CMUpdate,
    tags=["Condition Monitoring"],
    order_by="sampled_date", order_desc=True,
    filters={"monitoring_type": "monitoring_type", "result": "result", "equipment_id": "equipment_id"},
    not_found="Reading not found",
).router
