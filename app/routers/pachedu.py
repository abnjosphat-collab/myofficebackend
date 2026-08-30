#Pachedu
# backend/app/routers/pachedu.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, require_role
from app.aggregation import count_by
from app.db_helpers import get_or_404, apply_date_range, or_ilike, distinct_suggestions, status_choice_validator
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pachedu", tags=["Pachedu"])

# =============== PYDANTIC MODELS ===============

# Main Pachedu Report Model
class PacheduReportBase(BaseModel):
    location: str = Field(..., min_length=1)
    date: str = Field(..., min_length=1)
    activityObserved: str = Field(..., min_length=1)
    whatDidYouSee: str = Field(..., min_length=1)
    reasons: Optional[str] = ""
    behaviourType: str = Field(..., pattern="^(Intentional|Unintentional)$")
    impacts: List[str] = []
    whatDidYouDo: str = Field(..., min_length=1)
    observerName: Optional[str] = ""
    dept: Optional[str] = ""
    sdwt: Optional[str] = ""
    sectionChoice: str = Field(..., pattern="^(Mechanical|Electrical)$")
    checklist: List[str] = []
    status: str = "draft"

    _validate_status = validator('status')(status_choice_validator(
        ['draft', 'submitted', 'reviewed', 'closed'], 'Status must be draft, submitted, reviewed, or closed'))

class PacheduReportCreate(PacheduReportBase):
    pass

class PacheduReportUpdate(BaseModel):
    location: Optional[str] = None
    date: Optional[str] = None
    activityObserved: Optional[str] = None
    whatDidYouSee: Optional[str] = None
    reasons: Optional[str] = None
    behaviourType: Optional[str] = None
    impacts: Optional[List[str]] = None
    whatDidYouDo: Optional[str] = None
    observerName: Optional[str] = None
    dept: Optional[str] = None
    sdwt: Optional[str] = None
    sectionChoice: Optional[str] = None
    checklist: Optional[List[str]] = None
    status: Optional[str] = None

class PacheduReportResponse(PacheduReportBase):
    id: str
    created_at: str
    updated_at: Optional[str] = None
    submitted_at: Optional[str] = None

# Stats Models
class PacheduStatsResponse(BaseModel):
    total: int
    bySection: Dict[str, int]
    byDept: Dict[str, int]
    byBehaviour: Dict[str, int]
    totalImpacts: int
    totalChecklist: int
    draftCount: int
    submittedCount: int
    reviewedCount: int
    closedCount: int

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_pachedu_to_camel(db_item: dict) -> dict:
    """Map database column names (snake_case) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "location": db_item.get("location"),
        "date": db_item.get("date"),
        "activityObserved": db_item.get("activity_observed"),
        "whatDidYouSee": db_item.get("what_did_you_see"),
        "reasons": db_item.get("reasons", ""),
        "behaviourType": db_item.get("behaviour_type"),
        "impacts": db_item.get("impacts", []),
        "whatDidYouDo": db_item.get("what_did_you_do"),
        "observerName": db_item.get("observer_name", ""),
        "dept": db_item.get("dept", ""),
        "sdwt": db_item.get("sdwt", ""),
        "sectionChoice": db_item.get("section_choice"),
        "checklist": db_item.get("checklist", []),
        "status": db_item.get("status", "draft"),
        "created_at": db_item.get("created_at"),
        "updated_at": db_item.get("updated_at"),
        "submitted_at": db_item.get("submitted_at")
    }

# =============== API ENDPOINTS ===============

# GET all Pachedu reports
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_pachedu_reports(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    dept: Optional[str] = Query(None, description="Filter by department"),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    try:
        logger.info("Fetching Pachedu reports...")
        
        # Start with base query
        query = supabase.table("pachedu_reports").select("*")
        
        # Apply filters
        if search:
            query = query.or_(or_ilike(["observer_name", "location", "activity_observed", "what_did_you_see", "dept"], search))

        if section:
            query = query.eq("section_choice", section)

        if dept:
            query = query.ilike("dept", f"%{dept}%")

        if status:
            query = query.eq("status", status)

        query = apply_date_range(query, "date", from_date, to_date)

        # Order by most recent
        query = query.order("created_at", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()

        db_reports = rows(response)
        result = [map_db_pachedu_to_camel(report) for report in db_reports]
        return result
        
    except Exception as e:
        logger.error(f"Error fetching Pachedu reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# GET unique departments for auto-complete
@router.get("/suggestions/departments", dependencies=[Depends(get_current_user)])
async def get_department_suggestions(search: Optional[str] = Query(None)):
    return await distinct_suggestions(supabase, "pachedu_reports", "dept", search, "department")

# GET stats overview
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_pachedu_stats():
    try:
        logger.info("Fetching Pachedu stats...")
        
        # Get all reports
        reports_response = supabase.table("pachedu_reports").select("*").execute()
        reports = rows(reports_response)
        
        # Calculate stats
        total = len(reports)
        
        # Count by section
        by_section = {
            "Mechanical": 0,
            "Electrical": 0
        }
        
        # Count by behaviour
        by_behaviour = {
            "Intentional": 0,
            "Unintentional": 0
        }
        
        # Count by department
        by_dept = count_by((r.get("dept") for r in reports if r.get("dept")), lambda name: name)
        
        # Count by status
        draft_count = 0
        submitted_count = 0
        reviewed_count = 0
        closed_count = 0
        
        # Count impacts and checklist items
        total_impacts = 0
        total_checklist = 0
        
        for report in reports:
            # Section
            section = report.get("section_choice")
            if section in by_section:
                by_section[section] += 1
            
            # Behaviour
            behaviour = report.get("behaviour_type")
            if behaviour in by_behaviour:
                by_behaviour[behaviour] += 1
            
            # Status
            status = report.get("status", "draft")
            if status == "draft":
                draft_count += 1
            elif status == "submitted":
                submitted_count += 1
            elif status == "reviewed":
                reviewed_count += 1
            elif status == "closed":
                closed_count += 1
            
            # Impacts
            impacts = report.get("impacts", [])
            if impacts and isinstance(impacts, list):
                total_impacts += len(impacts)
            
            # Checklist
            checklist = report.get("checklist", [])
            if checklist and isinstance(checklist, list):
                total_checklist += len(checklist)
        
        return {
            "total": total,
            "bySection": by_section,
            "byDept": by_dept,
            "byBehaviour": by_behaviour,
            "totalImpacts": total_impacts,
            "totalChecklist": total_checklist,
            "draftCount": draft_count,
            "submittedCount": submitted_count,
            "reviewedCount": reviewed_count,
            "closedCount": closed_count
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")

# GET single report
@router.get("/{report_id}", dependencies=[Depends(get_current_user)])
async def get_pachedu_report(report_id: str):
    try:
        logger.info(f"Fetching Pachedu report {report_id}")
        
        # Get report
        report_response = supabase.table("pachedu_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        db_report = one_row(report_response)
        if db_report is None:
            raise HTTPException(status_code=404, detail="Report not found")

        camel_report = map_db_pachedu_to_camel(db_report)
        
        return camel_report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching report: {str(e)}")

# POST create report
@router.post("")
@router.post("/")
async def create_pachedu_report(report: PacheduReportCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating Pachedu report at location: {report.location}")
        
        report_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert report - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        report_data = {
            "id": report_id,
            "location": report.location,
            "date": report.date,
            "activity_observed": report.activityObserved,
            "what_did_you_see": report.whatDidYouSee,
            "reasons": report.reasons,
            "behaviour_type": report.behaviourType,
            "impacts": report.impacts,
            "what_did_you_do": report.whatDidYouDo,
            "observer_name": report.observerName,
            "dept": report.dept,
            "sdwt": report.sdwt,
            "section_choice": report.sectionChoice,
            "checklist": report.checklist,
            "status": report.status,
            "created_at": now,
            "updated_at": now,
            "submitted_at": now if report.status == "submitted" else None
        }
        
        logger.info(f"Inserting report: {report_data}")
        
        report_response = supabase.table("pachedu_reports")\
            .insert(report_data)\
            .execute()
        
        created_report = one_row(report_response)
        if created_report is None:
            raise HTTPException(status_code=500, detail="Failed to create report")
        
        # Map to camelCase for response
        result = map_db_pachedu_to_camel(created_report)
        
        logger.info(f"Successfully created Pachedu report with ID: {report_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating Pachedu report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# PATCH update report
@router.patch("/{report_id}")
async def update_pachedu_report(report_id: str, updated: PacheduReportUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating Pachedu report {report_id}")
        
        # Check if exists
        existing = supabase.table("pachedu_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Report not found")

        # Prepare update data - map camelCase to snake_case for DB
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map fields to snake_case column names
        field_mapping = {
            "location": "location",
            "date": "date",
            "activityObserved": "activity_observed",
            "whatDidYouSee": "what_did_you_see",
            "reasons": "reasons",
            "behaviourType": "behaviour_type",
            "impacts": "impacts",
            "whatDidYouDo": "what_did_you_do",
            "observerName": "observer_name",
            "dept": "dept",
            "sdwt": "sdwt",
            "sectionChoice": "section_choice",
            "checklist": "checklist",
            "status": "status"
        }
        
        for key, value in update_dict.items():
            # `value is not None` used to be here too — the null-vs-unset bug already
            # fixed project-wide once (backend edec24a): exclude_unset=True already
            # excludes fields the caller never sent, so an explicit null reaching here
            # IS the caller asking to clear that field. Fixed 2026-08-30.
            if key in field_mapping:
                data_to_update[field_mapping[key]] = value

        # Always update updated_at timestamp
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        # If status is being set to submitted, set submitted_at
        if "status" in update_dict and update_dict["status"] == "submitted":
            data_to_update["submitted_at"] = datetime.utcnow().isoformat()
        
        if data_to_update:
            update_response = supabase.table("pachedu_reports")\
                .update(data_to_update)\
                .eq("id", report_id)\
                .execute()
            
            updated_report = one_row(update_response)
            if updated_report is None:
                raise HTTPException(status_code=500, detail="Update failed")
        else:
            updated_report = existing_row
        
        # Map to camelCase for response
        result = map_db_pachedu_to_camel(updated_report)
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating report: {str(e)}")

# DELETE report
@router.delete("/{report_id}")
async def delete_pachedu_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting Pachedu report {report_id}")
        
        # Check if exists
        existing = supabase.table("pachedu_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()

        if one_row(existing) is None:
            raise HTTPException(status_code=404, detail="Report not found")

        supabase.table("pachedu_reports")\
            .delete()\
            .eq("id", report_id)\
            .execute()
        
        return {"success": True, "message": "Report deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting report: {str(e)}")
