# Work stoppage route
#backend/app/routerss//work_stoppage

# backend/app/routers/work_stoppage.py

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
router = APIRouter(prefix="/api/work-stoppage", tags=["Work Stoppage"])

# =============== PYDANTIC MODELS ===============

class CorrectiveActionBase(BaseModel):
    finding: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1)
    byWho: str = Field(..., min_length=1)
    byWhen: str = Field(..., min_length=1)
    status: str = "Pending"

    _validate_status = validator('status')(status_choice_validator(
        ['Pending', 'In Progress', 'Completed'], 'Status must be Pending, In Progress, or Completed'))

class CorrectiveActionCreate(CorrectiveActionBase):
    pass

class CorrectiveActionUpdate(BaseModel):
    finding: Optional[str] = None
    action: Optional[str] = None
    byWho: Optional[str] = None
    byWhen: Optional[str] = None
    status: Optional[str] = None

class CorrectiveActionResponse(CorrectiveActionBase):
    id: str
    report_id: str

class WorkStoppageBase(BaseModel):
    date: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    section: str = Field(..., pattern="^(Mechanical|Electrical|General)$")
    description: str = Field(..., min_length=10)
    investigationFindings: Optional[str] = ""
    stoppageBy: str = Field(..., min_length=1)
    stoppagePosition: Optional[str] = ""
    acceptedBy: Optional[str] = ""
    sheqCheckedBy: Optional[str] = ""

class WorkStoppageCreate(WorkStoppageBase):
    correctiveActions: List[CorrectiveActionCreate] = []

class WorkStoppageUpdate(BaseModel):
    date: Optional[str] = None
    department: Optional[str] = None
    section: Optional[str] = None
    description: Optional[str] = None
    investigationFindings: Optional[str] = None
    stoppageBy: Optional[str] = None
    stoppagePosition: Optional[str] = None
    acceptedBy: Optional[str] = None
    sheqCheckedBy: Optional[str] = None
    correctiveActions: Optional[List[CorrectiveActionUpdate]] = None

class WorkStoppageResponse(WorkStoppageBase):
    id: str
    correctiveActions: List[CorrectiveActionResponse] = []
    submittedAt: str

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_work_stoppage_to_camel(db_item: dict) -> dict:
    """Map database column names (lowercase) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "date": db_item.get("date"),
        "department": db_item.get("department"),
        "section": db_item.get("section"),
        "description": db_item.get("description"),
        "investigationFindings": db_item.get("investigation_findings", ""),
        "stoppageBy": db_item.get("stoppage_by"),
        "stoppagePosition": db_item.get("stoppage_position", ""),
        "acceptedBy": db_item.get("accepted_by", ""),
        "sheqCheckedBy": db_item.get("sheq_checked_by", ""),
        "submittedAt": db_item.get("submitted_at")
    }

def map_db_action_to_camel(db_item: dict) -> dict:
    """Map database column names (lowercase) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "report_id": db_item.get("report_id"),
        "finding": db_item.get("finding"),
        "action": db_item.get("action"),
        "byWho": db_item.get("by_who"),
        "byWhen": db_item.get("by_when"),
        "status": db_item.get("status")
    }

# =============== API ENDPOINTS ===============

# GET all work stoppage reports
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_reports(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    inspector: Optional[str] = Query(None, description="Filter by inspector name"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    try:
        logger.info("Fetching work stoppage reports...")
        
        # Start with base query
        query = supabase.table("work_stoppage_reports").select("*")
        
        # Apply filters
        if search:
            query = query.or_(or_ilike(["department", "description", "stoppage_by"], search))

        if section:
            query = query.eq("section", section)

        if inspector:
            query = query.ilike("stoppage_by", f"%{inspector}%")

        query = apply_date_range(query, "date", from_date, to_date)

        # Order by most recent
        query = query.order("submitted_at", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()

        if hasattr(response, 'data'):
            db_reports = rows(response)
            result = []

            # Fetch corrective actions for each report
            for report in db_reports:
                actions_response = supabase.table("corrective_actions")\
                    .select("*")\
                    .eq("report_id", report["id"])\
                    .execute()

                db_actions = rows(actions_response)
                camel_actions = [map_db_action_to_camel(a) for a in db_actions]
                
                camel_report = map_db_work_stoppage_to_camel(report)
                camel_report["correctiveActions"] = camel_actions
                result.append(camel_report)
            
            return result
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching work stoppage reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# GET unique departments for auto-complete
@router.get("/suggestions/departments", dependencies=[Depends(get_current_user)])
async def get_department_suggestions(search: Optional[str] = Query(None)):
    return await distinct_suggestions(supabase, "work_stoppage_reports", "department", search, "department")

# GET unique inspectors for auto-complete
@router.get("/suggestions/inspectors", dependencies=[Depends(get_current_user)])
async def get_inspector_suggestions(search: Optional[str] = Query(None)):
    return await distinct_suggestions(supabase, "work_stoppage_reports", "stoppage_by", search, "inspector")

# GET single report
@router.get("/{report_id}", dependencies=[Depends(get_current_user)])
async def get_report(report_id: str):
    try:
        logger.info(f"Fetching work stoppage report {report_id}")
        
        # Get report
        report_response = supabase.table("work_stoppage_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()

        db_report = one_row(report_response)
        if db_report is None:
            raise HTTPException(status_code=404, detail="Report not found")

        # Get corrective actions
        actions_response = supabase.table("corrective_actions")\
            .select("*")\
            .eq("report_id", report_id)\
            .execute()

        db_actions = rows(actions_response)
        camel_actions = [map_db_action_to_camel(a) for a in db_actions]

        camel_report = map_db_work_stoppage_to_camel(db_report)
        camel_report["correctiveActions"] = camel_actions
        
        return camel_report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching report: {str(e)}")

# POST create report
@router.post("")
@router.post("/")
async def create_report(report: WorkStoppageCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating work stoppage report for department: {report.department}")
        
        report_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert report - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        report_data = {
            "id": report_id,
            "date": report.date,
            "department": report.department,
            "section": report.section,
            "description": report.description,
            "investigation_findings": report.investigationFindings,
            "stoppage_by": report.stoppageBy,
            "stoppage_position": report.stoppagePosition,
            "accepted_by": report.acceptedBy,
            "sheq_checked_by": report.sheqCheckedBy,
            "submitted_at": now
        }
        
        logger.info(f"Inserting report: {report_data}")
        
        report_response = supabase.table("work_stoppage_reports")\
            .insert(report_data)\
            .execute()

        created_report = one_row(report_response)
        if created_report is None:
            raise HTTPException(status_code=500, detail="Failed to create report")
        
        # Insert corrective actions
        if report.correctiveActions:
            actions_data = []
            for action in report.correctiveActions:
                actions_data.append({
                    "id": generate_id(),
                    "report_id": report_id,
                    "finding": action.finding,
                    "action": action.action,
                    "by_who": action.byWho,
                    "by_when": action.byWhen,
                    "status": action.status
                })
            
            if actions_data:
                actions_response = supabase.table("corrective_actions")\
                    .insert(actions_data)\
                    .execute()

                db_actions = rows(actions_response)
                created_report["correctiveActions"] = [map_db_action_to_camel(a) for a in db_actions]
            else:
                created_report["correctiveActions"] = []
        else:
            created_report["correctiveActions"] = []
        
        # Map to camelCase for response
        result = map_db_work_stoppage_to_camel(created_report)
        result["correctiveActions"] = created_report.get("correctiveActions", [])
        
        logger.info(f"Successfully created work stoppage report with ID: {report_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating work stoppage report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# PATCH update report
@router.patch("/{report_id}")
async def update_report(report_id: str, updated: WorkStoppageUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating work stoppage report {report_id}")
        
        # Check if exists
        existing = supabase.table("work_stoppage_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Report not found")

        # Prepare update data - map camelCase to lowercase for DB
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map fields to lowercase column names
        field_mapping = {
            "date": "date",
            "department": "department",
            "section": "section",
            "description": "description",
            "investigationFindings": "investigation_findings",
            "stoppageBy": "stoppage_by",
            "stoppagePosition": "stoppage_position",
            "acceptedBy": "accepted_by",
            "sheqCheckedBy": "sheq_checked_by"
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None and key != "correctiveActions":
                data_to_update[field_mapping[key]] = value
        
        if data_to_update:
            update_response = supabase.table("work_stoppage_reports")\
                .update(data_to_update)\
                .eq("id", report_id)\
                .execute()

            updated_report = one_row(update_response)
            if updated_report is None:
                raise HTTPException(status_code=500, detail="Update failed")
        else:
            updated_report = existing_row
        
        # Update corrective actions if provided
        if updated.correctiveActions is not None:
            # Delete existing actions
            supabase.table("corrective_actions")\
                .delete()\
                .eq("report_id", report_id)\
                .execute()
            
            # Insert new actions
            if updated.correctiveActions:
                actions_data = []
                for action in updated.correctiveActions:
                    # updated.correctiveActions is a list of CorrectiveActionUpdate model
                    # instances, not dicts — .get() raised AttributeError on every PATCH
                    # that included correctiveActions (2026-08-30 fix, found via the
                    # rows()/one_row() pyright pass on this file).
                    # Only include if it has required fields
                    if action.finding and action.action and action.byWho and action.byWhen:
                        actions_data.append({
                            "id": generate_id(),
                            "report_id": report_id,
                            "finding": action.finding,
                            "action": action.action,
                            "by_who": action.byWho,
                            "by_when": action.byWhen,
                            "status": action.status or "Pending"
                        })
                
                if actions_data:
                    actions_response = supabase.table("corrective_actions")\
                        .insert(actions_data)\
                        .execute()

                    db_actions = rows(actions_response)
                    updated_report["correctiveActions"] = [map_db_action_to_camel(a) for a in db_actions]
                else:
                    updated_report["correctiveActions"] = []
            else:
                updated_report["correctiveActions"] = []
        else:
            # Get existing actions
            actions_response = supabase.table("corrective_actions")\
                .select("*")\
                .eq("report_id", report_id)\
                .execute()

            db_actions = rows(actions_response)
            updated_report["correctiveActions"] = [map_db_action_to_camel(a) for a in db_actions]
        
        # Map to camelCase for response
        result = map_db_work_stoppage_to_camel(updated_report)
        result["correctiveActions"] = updated_report.get("correctiveActions", [])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating report: {str(e)}")

# DELETE report
@router.delete("/{report_id}")
async def delete_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting work stoppage report {report_id}")
        
        # Check if exists
        existing = supabase.table("work_stoppage_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()

        if one_row(existing) is None:
            raise HTTPException(status_code=404, detail="Report not found")

        # Corrective actions will be automatically deleted due to foreign key cascade
        supabase.table("work_stoppage_reports")\
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
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        logger.info("Fetching work stoppage stats...")
        
        # Get all reports
        reports_response = supabase.table("work_stoppage_reports").select("*").execute()
        reports = rows(reports_response)

        # Get all actions
        actions_response = supabase.table("corrective_actions").select("*").execute()
        actions = rows(actions_response)
        
        # Calculate stats
        total = len(reports)
        
        # Count by section
        by_section = {
            "Mechanical": 0,
            "Electrical": 0,
            "General": 0
        }
        
        # Count by inspector
        by_inspector = count_by((r.get("stoppage_by") for r in reports if r.get("stoppage_by")), lambda name: name)

        # Count actions by status
        pending_actions = 0
        in_progress_actions = 0
        completed_actions = 0
        
        for report in reports:
            section = report.get("section")
            if section in by_section:
                by_section[section] += 1

        for action in actions:
            status = action.get("status")
            if status == "Pending":
                pending_actions += 1
            elif status == "In Progress":
                in_progress_actions += 1
            elif status == "Completed":
                completed_actions += 1
        
        return {
            "total": total,
            "bySection": by_section,
            "byInspector": by_inspector,
            "pendingActions": pending_actions,
            "inProgressActions": in_progress_actions,
            "completedActions": completed_actions
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")
