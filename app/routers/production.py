from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.supabase_client import supabase
import logging

router = APIRouter(tags=["Production"])
logger = logging.getLogger(__name__)

class ProductionCreate(BaseModel):
    prod_date: str
    shift: Optional[str] = None
    tonnes_milled: Optional[float] = None
    feed_rate_tph: Optional[float] = None
    grade_gpt: Optional[float] = None
    recovery_pct: Optional[float] = None
    gold_produced_oz: Optional[float] = None
    mill_availability: Optional[float] = None
    power_kwh: Optional[float] = None
    downtime_hours: Optional[float] = 0
    downtime_reason: Optional[str] = None
    operator: Optional[str] = None
    comments: Optional[str] = None

class ProductionUpdate(BaseModel):
    prod_date: Optional[str] = None
    shift: Optional[str] = None
    tonnes_milled: Optional[float] = None
    feed_rate_tph: Optional[float] = None
    grade_gpt: Optional[float] = None
    recovery_pct: Optional[float] = None
    gold_produced_oz: Optional[float] = None
    mill_availability: Optional[float] = None
    power_kwh: Optional[float] = None
    downtime_hours: Optional[float] = None
    downtime_reason: Optional[str] = None
    operator: Optional[str] = None
    comments: Optional[str] = None

@router.get("")
@router.get("/")
async def get_production(limit: int = 30, shift: Optional[str] = None):
    try:
        q = supabase.table("production_data").select("*").order("prod_date", desc=True).limit(limit)
        if shift: q = q.eq("shift", shift)
        return (q.execute()).data or []
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/stats/summary")
async def production_summary():
    """Last 30 days summary stats."""
    try:
        r = supabase.table("production_data").select("*").order("prod_date", desc=True).limit(30).execute()
        rows = r.data or []
        if not rows:
            return {"total_tonnes": 0, "avg_grade": 0, "avg_recovery": 0, "total_gold_oz": 0}
        total_t = sum(x.get("tonnes_milled") or 0 for x in rows)
        avg_g   = sum(x.get("grade_gpt")     or 0 for x in rows) / len(rows)
        avg_r   = sum(x.get("recovery_pct")  or 0 for x in rows) / len(rows)
        total_oz = sum(x.get("gold_produced_oz") or 0 for x in rows)
        return {
            "total_tonnes": round(total_t, 2),
            "avg_grade":    round(avg_g, 4),
            "avg_recovery": round(avg_r, 3),
            "total_gold_oz": round(total_oz, 4),
            "records": len(rows),
        }
    except Exception as e:
        raise HTTPException(500, str(e))

@router.post("")
@router.post("/")
async def create_production(data: ProductionCreate):
    try:
        r = supabase.table("production_data").insert(data.dict(exclude_none=True)).execute()
        if not r.data:
            raise HTTPException(500, "Insert failed")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

@router.patch("/{p_id}")
async def update_production(p_id: int, data: ProductionUpdate):
    payload = {k: v for k, v in data.dict().items() if v is not None}
    r = supabase.table("production_data").update(payload).eq("id", p_id).execute()
    if not r.data:
        raise HTTPException(404, "Record not found")
    return r.data[0]

@router.delete("/{p_id}")
async def delete_production(p_id: int):
    supabase.table("production_data").delete().eq("id", p_id).execute()
    return {"ok": True}
