from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import logging
from app.supabase_client import supabase
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── Pydantic model ───────────────────────────────────────────────────────────

class AvailRecordIn(BaseModel):
    equipment_id: int
    date: str                    # YYYY-MM-DD
    operational_hours: float
    breakdown_hours: float
    availability_percentage: float
    notes: Optional[str] = None

# ─── Existing read endpoints ──────────────────────────────────────────────────

@router.get("/availabilities")
async def get_availabilities():
    """Get equipment list with their latest availability data merged in."""
    try:
        logger.info("Fetching equipment with availability data...")
        equipment_response = supabase.table("equipment").select("*").execute()
        equipment = equipment_response.data if hasattr(equipment_response, 'data') else equipment_response

        for eq in equipment:
            av_resp = (supabase.table("availabilities")
                       .select("*")
                       .eq("equipment_id", eq["id"])
                       .order("date", desc=True)
                       .limit(1)
                       .execute())
            if av_resp.data:
                latest = av_resp.data[0]
                eq["availability"]          = latest["availability_percentage"]
                eq["operational_hours"]     = latest["operational_hours"]
                eq["breakdown_hours"]       = latest["breakdown_hours"]
                eq["status"]               = latest.get("status", eq.get("status", "operational"))
                eq["uptime"]               = latest["operational_hours"] - latest["breakdown_hours"]
                eq["downtime"]             = latest["breakdown_hours"]
                eq["mtbf"]                 = latest.get("mtbf", 100)
                eq["mttr"]                 = latest.get("mttr", 4)
                eq["last_maintenance"]     = latest.get("date")
            else:
                eq["availability"]      = 100.0
                eq["operational_hours"] = eq.get("operational_hours", 0)
                eq["breakdown_hours"]   = eq.get("breakdown_hours", 0)
                eq["status"]           = eq.get("status", "operational")
                eq["uptime"]           = eq.get("operational_hours", 0) - eq.get("breakdown_hours", 0)
                eq["downtime"]         = eq.get("breakdown_hours", 0)
                eq["mtbf"]             = 100
                eq["mttr"]             = 4
                eq["last_maintenance"] = eq.get("last_maintenance_date")

        return equipment
    except Exception as e:
        logger.error(f"Error fetching availabilities: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching availabilities: {e}")


@router.get("/availabilities/stats")
async def get_availability_stats():
    """Aggregate availability statistics."""
    try:
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        equipment_response = supabase.table("equipment").select("*").execute()
        equipment = equipment_response.data if hasattr(equipment_response, 'data') else equipment_response

        if not equipment:
            return {"totalEquipment": 0, "operational": 0, "inMaintenance": 0, "inBreakdown": 0,
                    "overallAvailability": 0, "avgUptime": 0, "avgDowntime": 0,
                    "totalOperationalHours": 0, "totalBreakdownHours": 0,
                    "monthAvailability": 0, "weekAvailability": 0}

        total = len(equipment)
        operational   = sum(1 for e in equipment if e.get("status") == "operational")
        maintenance   = sum(1 for e in equipment if e.get("status") == "maintenance")
        breakdown     = sum(1 for e in equipment if e.get("status") == "breakdown")
        total_op      = sum(e.get("operational_hours", 0) or 0 for e in equipment)
        total_bd      = sum(e.get("breakdown_hours", 0) or 0 for e in equipment)
        overall_av    = ((total_op - total_bd) / total_op * 100) if total_op > 0 else 0

        return {
            "totalEquipment":        total,
            "operational":           operational,
            "inMaintenance":         maintenance,
            "inBreakdown":           breakdown,
            "overallAvailability":   round(overall_av, 2),
            "avgUptime":             round((total_op - total_bd) / total, 2) if total else 0,
            "avgDowntime":           round(total_bd / total, 2) if total else 0,
            "totalOperationalHours": round(total_op, 2),
            "totalBreakdownHours":   round(total_bd, 2),
            "monthAvailability":     round(overall_av, 2),
            "weekAvailability":      round(overall_av * 0.98, 2),
        }
    except Exception as e:
        logger.error(f"Error calculating stats: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {e}")


@router.get("/availabilities/history/{equipment_id}")
async def get_availability_history(equipment_id: int, days: int = 30):
    """Availability history for one piece of equipment."""
    try:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        response = (supabase.table("availabilities")
                    .select("*")
                    .eq("equipment_id", equipment_id)
                    .gte("date", start_date)
                    .order("date", desc=True)
                    .execute())
        return response.data if hasattr(response, 'data') else response
    except Exception as e:
        logger.error(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {e}")


# ─── CRUD for availability records ────────────────────────────────────────────

@router.get("/availability-records")
async def list_availability_records(
    equipment_id: Optional[int] = None,
    date_from:    Optional[str] = None,
    date_to:      Optional[str] = None,
):
    """List all availability records with optional filters."""
    try:
        q = supabase.table("availabilities").select("*").order("date", desc=True)
        if equipment_id: q = q.eq("equipment_id", equipment_id)
        if date_from:    q = q.gte("date", date_from)
        if date_to:      q = q.lte("date", date_to)
        r = q.execute()
        rows = r.data or []

        # Try to enrich with equipment name
        eq_resp = supabase.table("equipment").select("id, name").execute()
        eq_lookup = {str(e["id"]): e["name"] for e in (eq_resp.data or [])}
        for row in rows:
            row["equipment_name"] = eq_lookup.get(str(row.get("equipment_id", "")))

        return rows
    except Exception as e:
        logger.error(f"list_availability_records error: {e}")
        raise HTTPException(500, str(e))


@router.get("/availability-records/from-breakdowns")
async def availability_from_breakdowns(
    equipment_id: Optional[int] = None,
    date_from:    Optional[str] = None,
    date_to:      Optional[str] = None,
):
    """Compute availability records derived from breakdown downtime entries."""
    try:
        from collections import defaultdict

        # Load equipment: equipment_id (code) → row
        eq_resp = supabase.table("equipment").select("id, name, equipment_id, category, department").execute()
        equipment = eq_resp.data or []
        code_to_eq = {e["equipment_id"]: e for e in equipment}
        name_to_eq = {e["name"]: e for e in equipment}

        # Load breakdowns
        q = supabase.table("breakdowns").select("machine_id, machine_name, breakdown_date, downtime_minutes")
        if date_from: q = q.gte("breakdown_date", date_from)
        if date_to:   q = q.lte("breakdown_date", date_to)
        bd_resp = q.execute()
        breakdowns = bd_resp.data or []

        # Group by (machine_id_code, date) and sum downtime_minutes
        grouped: dict = defaultdict(lambda: {"bd_minutes": 0.0, "machine_name": ""})
        for bd in breakdowns:
            key = (bd.get("machine_id", ""), bd.get("breakdown_date", ""))
            grouped[key]["bd_minutes"] += float(bd.get("downtime_minutes") or 0)
            grouped[key]["machine_name"] = bd.get("machine_name") or ""

        DAILY_OP_HOURS = 24.0
        records = []
        for (machine_code, date), v in grouped.items():
            if not date:
                continue
            # Match equipment by code, then by name
            eq = code_to_eq.get(machine_code) or name_to_eq.get(v["machine_name"])
            eq_id  = eq["id"]   if eq else None
            eq_name = eq["name"] if eq else (v["machine_name"] or machine_code)

            if equipment_id and eq_id != equipment_id:
                continue

            bd_hours = round(min(v["bd_minutes"] / 60.0, DAILY_OP_HOURS), 2)
            pct = round(((DAILY_OP_HOURS - bd_hours) / DAILY_OP_HOURS) * 100, 2)

            records.append({
                "id":                     f"bd_{machine_code}_{date}",
                "equipment_id":           eq_id if eq_id is not None else machine_code,
                "equipment_name":         eq_name,
                "date":                   date,
                "operational_hours":      DAILY_OP_HOURS,
                "breakdown_hours":        bd_hours,
                "availability_percentage": pct,
                "notes":                  "Auto-computed from breakdowns",
                "source":                 "breakdown",
            })

        records.sort(key=lambda r: r["date"], reverse=True)
        return records

    except Exception as e:
        logger.error(f"availability_from_breakdowns error: {e}")
        raise HTTPException(500, str(e))


@router.post("/availability-records")
async def create_availability_record(body: AvailRecordIn):
    """Create a new availability record."""
    try:
        now  = datetime.utcnow().isoformat()
        data = body.model_dump()
        data.pop("availability_percentage", None)   # generated column — DB computes it
        data["created_at"] = now
        data["updated_at"] = now
        r = supabase.table("availabilities").insert(data).execute()
        return r.data[0]
    except Exception as e:
        logger.error(f"create_availability_record error: {e}")
        raise HTTPException(500, str(e))


@router.put("/availability-records/{record_id}")
async def update_availability_record(record_id: int, body: AvailRecordIn):
    """Update an existing availability record."""
    try:
        data = body.model_dump()
        data.pop("availability_percentage", None)   # generated column — DB computes it
        data["updated_at"] = datetime.utcnow().isoformat()
        r = (supabase.table("availabilities")
             .update(data)
             .eq("id", record_id)
             .execute())
        if not r.data:
            raise HTTPException(404, "Record not found")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"update_availability_record error: {e}")
        raise HTTPException(500, str(e))


@router.delete("/availability-records/{record_id}")
async def delete_availability_record(record_id: int):
    """Delete an availability record."""
    try:
        supabase.table("availabilities").delete().eq("id", record_id).execute()
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete_availability_record error: {e}")
        raise HTTPException(500, str(e))
