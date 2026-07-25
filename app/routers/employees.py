# backend/app/routers/employees.py
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.cache import cached, invalidate_namespace
from app.rate_limit import limiter

router = APIRouter()


class Employee(BaseModel):
    """
    Employee record. `employee_id` is the user-visible identifier
    (e.g. "C1165", "PM365") and is freely editable.
    The immutable database primary key is the integer `id` returned
    in API responses — the frontend uses that for all update/delete calls.
    """
    employee_id: str = Field(..., min_length=1, max_length=50,
                             description="Human-readable employee ID, e.g. C1165 or PM365")
    first_name: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    id_number: str = Field(..., min_length=1, description="National ID or passport number")
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    date_of_engagement: date = Field(..., description="Date of employment")
    designation: str = Field(..., min_length=1, description="Job title / position")
    employee_class: Optional[str] = None  # Permanent, Contract, Internship, Part-Time
    employment_type: Optional[str] = None  # NEC or SALARIED
    discipline: Optional[str] = None  # mechanical or electrical (trade discipline)
    supervisor: Optional[str] = None
    section: Optional[str] = None
    department: Optional[str] = None
    grade: Optional[str] = None
    qualifications: Optional[List[str]] = Field(default_factory=list)
    drivers_license_class: Optional[str] = None
    ppe_issue_date: Optional[date] = None
    offences: Optional[List[str]] = Field(default_factory=list)
    awards_recognition: Optional[List[str]] = Field(default_factory=list)
    other_positions: Optional[List[str]] = Field(default_factory=list)
    previous_employer: Optional[str] = None

    class Config:
        json_encoders = {date: lambda v: v.isoformat()}

    @validator('employee_id')
    def clean_employee_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError('Employee ID cannot be empty')
        # Allow any alphanumeric ID: C1165, PM365, EMP-042, etc.
        return v.strip().upper()

    @validator('qualifications', 'offences', 'awards_recognition', 'other_positions',
               pre=True, always=True)
    def ensure_list(cls, v):
        return v if isinstance(v, list) else []


# ── Helpers ──────────────────────────────────────────────────────────────────

def _dates_to_db(data: dict) -> dict:
    """Convert date objects → ISO strings for Supabase."""
    out = data.copy()
    for field in ('date_of_engagement', 'ppe_issue_date'):
        if isinstance(out.get(field), date):
            out[field] = out[field].isoformat()
    return out


def _dates_from_db(data: dict) -> dict:
    """Convert ISO date strings → date objects; normalise arrays."""
    out = data.copy()
    for field in ('date_of_engagement', 'ppe_issue_date'):
        if out.get(field) and isinstance(out[field], str):
            try:
                out[field] = date.fromisoformat(out[field])
            except (ValueError, TypeError):
                out[field] = None
    for field in ('qualifications', 'offences', 'awards_recognition', 'other_positions'):
        if not isinstance(out.get(field), list):
            out[field] = []
    return out


def _data(response) -> list:
    return response.data if hasattr(response, 'data') else response


# ── Routes ────────────────────────────────────────────────────────────────────
# NOTE: health/status and search/{query} are declared BEFORE /{id} so FastAPI
#       matches them first (they contain non-integer path segments).

@router.get("/health/status", tags=["Health"])
async def employees_health():
    try:
        _data(supabase.table("employees").select("id").limit(1).execute())
        return {"status": "healthy", "service": "employees", "database": "connected"}
    except Exception as e:
        raise HTTPException(503, detail=f"Employees service unhealthy: {e}")


@router.get("/search/{query}", dependencies=[Depends(get_current_user)])
async def search_employees(
    query: str,
    search_by: str = Query("all", enum=["all", "name", "id", "id_number", "email"])
):
    """Search employees by various fields."""
    try:
        q = f"%{query}%"
        if search_by == "name":
            r = supabase.table("employees").select("*") \
                .or_(f"first_name.ilike.{q},last_name.ilike.{q}").execute()
        elif search_by == "id":
            r = supabase.table("employees").select("*").ilike("employee_id", q).execute()
        elif search_by == "id_number":
            r = supabase.table("employees").select("*").ilike("id_number", q).execute()
        elif search_by == "email":
            r = supabase.table("employees").select("*").ilike("email", q).execute()
        else:
            r = supabase.table("employees").select("*") \
                .or_(f"first_name.ilike.{q},last_name.ilike.{q},"
                     f"employee_id.ilike.{q},id_number.ilike.{q}").execute()

        rows = _data(r)
        return [_dates_from_db(e) for e in rows] if rows else []
    except Exception as e:
        raise HTTPException(500, detail=f"Search error: {e}")


@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
@cached("employees", ttl=60)
async def get_employees():
    """Return all employees."""
    try:
        rows = _data(supabase.table("employees").select("*").execute())
        return [_dates_from_db(e) for e in rows] if rows else []
    except Exception as e:
        raise HTTPException(500, detail=f"Error fetching employees: {e}")


@router.get("/{id}", dependencies=[Depends(get_current_user)])
async def get_employee(id: int):
    """Return a single employee by their database ID."""
    try:
        rows = _data(supabase.table("employees").select("*").eq("id", id).execute())
        if not rows:
            raise HTTPException(404, detail=f"Employee #{id} not found")
        return _dates_from_db(rows[0])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Error fetching employee: {e}")


@router.post("")
@router.post("/")
async def create_employee(employee: Employee, current_user: dict = Depends(get_current_user)):
    """Create a new employee. The employee_id must be unique."""
    try:
        # Reject duplicate employee_id
        clash = _data(
            supabase.table("employees")
            .select("id")
            .eq("employee_id", employee.employee_id)
            .execute()
        )
        if clash:
            raise HTTPException(
                400,
                detail=f"Employee ID '{employee.employee_id}' is already in use. "
                       "Choose a different ID."
            )

        payload = _dates_to_db(employee.dict())
        rows = _data(supabase.table("employees").insert(payload).execute())
        if not rows:
            raise HTTPException(500, detail="No data returned after insertion")
        await invalidate_namespace("employees")
        return _dates_from_db(rows[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Error creating employee: {e}")


@router.put("/{id}")
async def update_employee(id: int, updated: Employee, current_user: dict = Depends(get_current_user)):
    """
    Update an employee record identified by the integer database ID.
    employee_id in the request body can be freely changed to any value
    (e.g. from EMP_1165 to C1165) as long as it isn't already used by
    a *different* employee.
    """
    try:
        # Confirm the employee exists
        existing_rows = _data(
            supabase.table("employees").select("id,employee_id").eq("id", id).execute()
        )
        if not existing_rows:
            raise HTTPException(404, detail=f"Employee #{id} not found")

        old_emp_id = existing_rows[0]['employee_id']
        new_emp_id = updated.employee_id

        # If employee_id is changing, make sure the new one isn't taken
        if new_emp_id != old_emp_id:
            clash = _data(
                supabase.table("employees")
                .select("id")
                .eq("employee_id", new_emp_id)
                .neq("id", id)
                .execute()
            )
            if clash:
                raise HTTPException(
                    400,
                    detail=f"Employee ID '{new_emp_id}' is already used by another employee."
                )

        payload = _dates_to_db(updated.dict())
        rows = _data(
            supabase.table("employees").update(payload).eq("id", id).execute()
        )
        if not rows:
            raise HTTPException(500, detail="No data returned after update")
        await invalidate_namespace("employees")
        return _dates_from_db(rows[0])

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Error updating employee: {e}")


@router.delete("/{id}")
async def delete_employee(id: int, current_user: dict = Depends(require_role('manager'))):
    """Delete an employee by their database ID."""
    try:
        existing_rows = _data(
            supabase.table("employees")
            .select("id,employee_id,first_name,last_name")
            .eq("id", id)
            .execute()
        )
        if not existing_rows:
            raise HTTPException(404, detail=f"Employee #{id} not found")

        emp = existing_rows[0]
        name = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip() or "Unknown"

        supabase.table("employees").delete().eq("id", id).execute()
        await invalidate_namespace("employees")

        return {
            "success": True,
            "detail": f"{name} ({emp['employee_id']}) deleted",
            "deleted_id": id,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=f"Error deleting employee: {e}")


class BulkDisciplineRequest(BaseModel):
    ids: List[int]
    discipline: Optional[str] = None  # 'mechanical' | 'electrical' | None/'' to clear


@router.post("/bulk-discipline")
@limiter.limit("10/minute")
async def bulk_set_discipline(request: Request, payload: BulkDisciplineRequest, current_user: dict = Depends(get_current_user)):
    """Set the trade discipline on many employees in one call (bulk mechanical/electrical)."""
    if payload.discipline not in (None, '', 'mechanical', 'electrical'):
        raise HTTPException(400, detail="discipline must be 'mechanical', 'electrical', or empty")
    if not payload.ids:
        return {"updated": 0, "discipline": payload.discipline or None}
    disc = payload.discipline or None
    try:
        supabase.table("employees").update({"discipline": disc}).in_("id", payload.ids).execute()
        await invalidate_namespace("employees")
        return {"updated": len(payload.ids), "discipline": disc}
    except Exception as e:
        raise HTTPException(500, detail=f"Could not set discipline — has the 'discipline' column been added? {e}")
