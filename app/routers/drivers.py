"""
Drivers Router — authorised mine drivers registry

Migrated onto the shared CrudRouter (see app/crud_router.py) — this was a plain
list/create/update/delete router with no computed fields, joins, or cross-table
side effects, a genuine CrudRouter fit. The two write-time behaviours the original
hand-written version had (trimming full_name/phone_numbers, collapsing an empty
string back to None, stamping updated_at — this table has no DB-level trigger for
it) are preserved via before_create/before_update hooks, not dropped.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.crud_router import CrudRouter

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


def _clean_driver_write(data: dict) -> dict:
    """Mirrors the original hand-written router's write-time normalization: trim
    full_name, drop/trim blank phone numbers, and collapse an empty-string optional
    field back to None (CrudRouter's create path already drops actual None values via
    exclude_none, but not ""; the update path passes "" through as a real value since
    it's a legitimate explicit clear-to-empty otherwise indistinguishable from unset)."""
    if isinstance(data.get("full_name"), str):
        data["full_name"] = data["full_name"].strip()
    if isinstance(data.get("phone_numbers"), list):
        data["phone_numbers"] = [p.strip() for p in data["phone_numbers"] if isinstance(p, str) and p.strip()]
    for field in ("department", "license_class", "license_expiry", "notes"):
        if data.get(field) == "":
            data[field] = None
    return data


def _before_create(data: dict) -> dict:
    data = _clean_driver_write(data)
    now = datetime.utcnow().isoformat()
    data["created_at"] = now
    data["updated_at"] = now
    return data


def _before_update(data: dict) -> dict:
    data = _clean_driver_write(data)
    data["updated_at"] = datetime.utcnow().isoformat()
    return data


router = CrudRouter(
    "drivers", DriverCreate, DriverUpdate,
    # No tags= here — main.py's register_router("drivers", ...) already supplies
    # ["Drivers"] via include_router; setting it again here would double-tag every
    # route in the OpenAPI docs.
    order_by="full_name",
    filters={"department": "department", "status": "status"},
    search_columns=["full_name", "department", "license_class"],
    default_limit=1000,
    not_found="Driver not found",
    before_create=_before_create,
    before_update=_before_update,
).router
