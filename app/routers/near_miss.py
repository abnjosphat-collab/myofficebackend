# backend/app/routers/near_miss.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
# Updated prefix to include /api
router = APIRouter(prefix="/api/nearmiss", tags=["Near Miss Reports"])

# =============== PYDANTIC MODELS ===============

class NearMissBase(BaseModel):
    department: str = Field(..., min_length=1)
    section: str = Field(..., pattern="^(Mechanical|Electrical|General)$")
    date: str = Field(..., min_length=1)
    time: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    description: str = Field(..., min_length=10)
    witnessDetails: Optional[str] = ""
    reporterName: Optional[str] = ""

class NearMissCreate(NearMissBase):
    pass

class NearMissUpdate(BaseModel):
    department: Optional[str] = None
    section: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    witnessDetails: Optional[str] = None
    reporterName: Optional[str] = None

class NearMissResponse(NearMissBase):
    id: str
    submittedAt: str
    
    class Config:
        from_attributes = True

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_to_camel(db_item: dict) -> dict:
    """Map database column names (lowercase) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "department": db_item.get("department"),
        "section": db_item.get("section"),
        "date": db_item.get("date"),
        "time": db_item.get("time"),
        "location": db_item.get("location"),
        "description": db_item.get("description"),
        "witnessDetails": db_item.get("witnessdetails", ""),
        "reporterName": db_item.get("reportername", ""),
        "submittedAt": db_item.get("submitted_at")
    }

# =============== API ENDPOINTS ===============

# GET all near miss reports
@router.get("")
@router.get("/")
async def get_reports(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    reporter: Optional[str] = Query(None, description="Filter by reporter name"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    try:
        logger.info("Fetching near miss reports...")
        
        # Start with base query
        query = supabase.table("nearmiss_reports").select("*")
        
        # Apply filters
        if search:
            query = query.or_(
                f"department.ilike.%{search}%," +
                f"location.ilike.%{search}%," +
                f"description.ilike.%{search}%"
            )
        
        if section:
            query = query.eq("section", section)
        
        if reporter:
            query = query.ilike("reportername", f"%{reporter}%")
        
        if from_date:
            query = query.gte("date", from_date)
        
        if to_date:
            query = query.lte("date", to_date)
        
        # Order by most recent
        query = query.order("submitted_at", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()
        
        logger.info(f"Supabase response: {response}")
        
        if hasattr(response, 'data'):
            db_reports = response.data or []
            # Map to camelCase for frontend
            return [map_db_to_camel(report) for report in db_reports]
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching near miss reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# GET single report
@router.get("/{report_id}")
async def get_report(report_id: str):
    try:
        logger.info(f"Fetching near miss report {report_id}")
        
        response = supabase.table("nearmiss_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        return map_db_to_camel(response.data[0])
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching report: {str(e)}")

# POST create report
@router.post("")
@router.post("/")
async def create_report(report: NearMissCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating near miss report for department: {report.department}")
        
        report_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert report - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        report_data = {
            "id": report_id,
            "department": report.department,
            "section": report.section,
            "date": report.date,
            "time": report.time,
            "location": report.location,
            "description": report.description,
            "witnessdetails": report.witnessDetails,  # lowercase
            "reportername": report.reporterName,      # lowercase
            "submitted_at": now
        }
        
        logger.info(f"Inserting report: {report_data}")
        
        response = supabase.table("nearmiss_reports")\
            .insert(report_data)\
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create report")
        
        created_report = response.data[0]
        result = map_db_to_camel(created_report)
        
        logger.info(f"Successfully created near miss report with ID: {report_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating near miss report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# PATCH update report
@router.patch("/{report_id}")
async def update_report(report_id: str, updated: NearMissUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating near miss report {report_id}")
        
        # Check if exists
        existing = supabase.table("nearmiss_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Prepare update data - map camelCase to lowercase for DB
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map fields to lowercase column names
        field_mapping = {
            "department": "department",
            "section": "section",
            "date": "date",
            "time": "time",
            "location": "location",
            "description": "description",
            "witnessDetails": "witnessdetails",
            "reporterName": "reportername"
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None:
                data_to_update[field_mapping[key]] = value
        
        if not data_to_update:
            return map_db_to_camel(existing.data[0])
        
        response = supabase.table("nearmiss_reports")\
            .update(data_to_update)\
            .eq("id", report_id)\
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Update failed")
        
        updated_report = response.data[0]
        return map_db_to_camel(updated_report)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating report: {str(e)}")

# DELETE report
@router.delete("/{report_id}")
async def delete_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting near miss report {report_id}")
        
        # Check if exists
        existing = supabase.table("nearmiss_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        supabase.table("nearmiss_reports")\
            .delete()\
            .eq("id", report_id)\
            .execute()
        
        return {"success": True, "message": "Report deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting report: {str(e)}")

# GET stats
@router.get("/stats/overview")
async def get_stats():
    try:
        logger.info("Fetching near miss stats...")
        
        response = supabase.table("nearmiss_reports")\
            .select("*")\
            .execute()
        
        reports = response.data if hasattr(response, 'data') else []
        
        # Calculate stats
        total = len(reports)
        
        # Count by section
        by_section = {
            "Mechanical": 0,
            "Electrical": 0,
            "General": 0
        }
        
        # Count by reporter
        by_reporter = {}
        
        for report in reports:
            section = report.get("section")
            if section in by_section:
                by_section[section] += 1
            
            reporter = report.get("reportername")
            if reporter:
                by_reporter[reporter] = by_reporter.get(reporter, 0) + 1
        
        return {
            "total": total,
            "bySection": by_section,
            "byReporter": by_reporter
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")

# Test endpoint to verify router is working
@router.get("/test")
async def test_endpoint():
    return {
        "status": "success", 
        "message": "Near Miss router is working!",
        "timestamp": datetime.utcnow().isoformat()
    }

# Debug endpoint
@router.get("/debug/test")
async def debug_test():
    try:
        # Test select
        select_result = supabase.table("nearmiss_reports").select("*").execute()
        
        # Test insert
        test_id = generate_id()
        now = datetime.utcnow().isoformat()
        test_data = {
            "id": test_id,
            "department": "Test Department",
            "section": "General",
            "date": "2024-01-15",
            "time": "10:00",
            "location": "Test Location",
            "description": "This is a test near miss report",
            "witnessdetails": "Test Witness",
            "reportername": "Test Reporter",
            "submitted_at": now
        }
        
        insert_result = supabase.table("nearmiss_reports").insert(test_data).execute()
        
        # Clean up test data
        if insert_result.data:
            supabase.table("nearmiss_reports").delete().eq("id", test_id).execute()
        
        return {
            "status": "success",
            "table_exists": True,
            "current_records": len(select_result.data) if select_result.data else 0,
            "insert_test": "success" if insert_result.data else "failed"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }