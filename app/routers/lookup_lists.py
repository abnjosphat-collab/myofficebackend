# backend/app/routers/lookup_lists.py — generic "pick from a growing list, or type
# a new value and have it remembered" backing store. One table (lookup_lists,
# list_name + value) serves every field that wants this instead of a bespoke table
# per field — today's consumers are breakdowns' `location` and `breakdown_nature`
# (see backend/supabase_migration_lookup_lists.sql for the schema).
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from app.supabase_client import supabase
from app.auth import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


class LookupValueCreate(BaseModel):
    value: str = Field(..., min_length=1)


@router.get("/{list_name}")
async def get_lookup_list(list_name: str):
    """All values saved for this list, alphabetical."""
    try:
        r = supabase.table("lookup_lists").select("id, value").eq("list_name", list_name).order("value").execute()
        return r.data or []
    except Exception as e:
        logger.error(f"Failed to fetch lookup list '{list_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{list_name}", dependencies=[Depends(get_current_user)])
async def add_lookup_value(list_name: str, body: LookupValueCreate):
    """Idempotent add — a case-insensitive match already on the list is returned
    as-is rather than creating a duplicate entry."""
    value = body.value.strip()
    try:
        existing = supabase.table("lookup_lists").select("id, value").eq("list_name", list_name).ilike("value", value).execute()
        if existing.data:
            return existing.data[0]
        r = supabase.table("lookup_lists").insert({"list_name": list_name, "value": value}).execute()
        return r.data[0] if r.data else {"list_name": list_name, "value": value}
    except Exception as e:
        logger.error(f"Failed to add value to lookup list '{list_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
