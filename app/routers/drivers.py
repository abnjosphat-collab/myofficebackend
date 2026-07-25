"""
Drivers Router — authorised mine drivers registry
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# Run this SQL in Supabase before using this router:
#
#   CREATE TABLE drivers (
#     id SERIAL PRIMARY KEY,
#     full_name TEXT NOT NULL,
#     phone_numbers JSONB NOT NULL DEFAULT '[]',
#     department TEXT,
#     license_class TEXT,
#     license_expiry DATE,
#     status TEXT NOT NULL DEFAULT 'active',
#     notes TEXT,
#     created_at TIMESTAMPTZ DEFAULT NOW(),
#     updated_at TIMESTAMPTZ DEFAULT NOW()
#   );
#   CREATE INDEX idx_drivers_status ON drivers(status);
#   CREATE INDEX idx_drivers_department ON drivers(department);


class DriverCreate(BaseModel):
    full_name: str = Field(..., min_length=1)
    phone_numbers: List[str] = Field(default_factory=list)
    department: Optional[str] = None
    license_class: Optional[str] = None
    license_expiry: Optional[str] = None
    status: str = Field("active")
    notes: Optional[str] = None


class DriverUpdate(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1)
    phone_numbers: Optional[List[str]] = None
    department: Optional[str] = None
    license_class: Optional[str] = None
    license_expiry: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


def _clean(row: dict) -> dict:
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            row[k] = v.isoformat()
    return row


@router.get("", dependencies=[Depends(get_current_user)])
async def get_drivers(
    search: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0),
):
    try:
        query = supabase.table("drivers").select("*")
        if search:
            query = query.or_(
                f"full_name.ilike.%{search}%,"
                f"department.ilike.%{search}%,"
                f"license_class.ilike.%{search}%"
            )
        if department:
            query = query.eq("department", department)
        if status:
            query = query.eq("status", status)
        query = query.order("full_name", desc=False).limit(limit).offset(offset)
        response = query.execute()
        return [_clean(r) for r in (response.data or [])]
    except Exception as e:
        logger.error(f"Error fetching drivers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", status_code=201)
async def create_driver(driver: DriverCreate, current_user: dict = Depends(get_current_user)):
    try:
        now = datetime.utcnow().isoformat()
        row = {
            "full_name": driver.full_name.strip(),
            "phone_numbers": [p.strip() for p in driver.phone_numbers if p.strip()],
            "department": driver.department or None,
            "license_class": driver.license_class or None,
            "license_expiry": driver.license_expiry or None,
            "status": driver.status,
            "notes": driver.notes or None,
            "created_at": now,
            "updated_at": now,
        }
        response = supabase.table("drivers").insert(row).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create driver")
        return _clean(response.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating driver: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{driver_id}")
async def update_driver(driver_id: int, driver: DriverUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = supabase.table("drivers").select("id").eq("id", driver_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Driver not found")

        patch = driver.dict(exclude_unset=True)
        if "phone_numbers" in patch:
            patch["phone_numbers"] = [p.strip() for p in patch["phone_numbers"] if p.strip()]
        if "full_name" in patch:
            patch["full_name"] = patch["full_name"].strip()
        patch["updated_at"] = datetime.utcnow().isoformat()

        response = supabase.table("drivers").update(patch).eq("id", driver_id).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update driver")
        return _clean(response.data[0])
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating driver: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{driver_id}")
async def delete_driver(driver_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = supabase.table("drivers").select("id, full_name").eq("id", driver_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Driver not found")
        supabase.table("drivers").delete().eq("id", driver_id).execute()
        return {"message": "Deleted", "id": driver_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting driver: {e}")
        raise HTTPException(status_code=500, detail=str(e))
