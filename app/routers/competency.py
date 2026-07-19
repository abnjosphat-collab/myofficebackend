from typing import Optional
from pydantic import BaseModel
from app.crud_router import CrudRouter


class CompetencyCreate(BaseModel):
    employee_id: str
    employee_name: str
    trade: Optional[str] = None
    equipment_type: str
    skill_area: str
    skill_level: int = 0
    certified: bool = False
    cert_date: Optional[str] = None
    cert_expiry: Optional[str] = None
    certified_by: Optional[str] = None
    notes: Optional[str] = None


class CompetencyUpdate(BaseModel):
    trade: Optional[str] = None
    equipment_type: Optional[str] = None
    skill_area: Optional[str] = None
    skill_level: Optional[int] = None
    certified: Optional[bool] = None
    cert_date: Optional[str] = None
    cert_expiry: Optional[str] = None
    certified_by: Optional[str] = None
    notes: Optional[str] = None


# Standard CRUD over competency_matrix — see app/crud_router.py. Filters and ordering
# match the previous hand-written handlers exactly (order by employee_name; eq filters
# on employee_id / trade / equipment_type).
router = CrudRouter(
    "competency_matrix", CompetencyCreate, CompetencyUpdate,
    tags=["Competency Matrix"],
    order_by="employee_name",
    filters={"employee_id": "employee_id", "trade": "trade", "equipment_type": "equipment_type"},
    not_found="Entry not found",
).router
