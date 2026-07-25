# vfl router
# backend/app/routers/vfl.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vfl", tags=["VFL"])

# =============== PYDANTIC MODELS ===============

# Action Item Model
class ActionItemBase(BaseModel):
    action: str = Field(..., min_length=1)
    responsible: str = Field(..., min_length=1)
    targetDate: str = Field(..., min_length=1)
    status: str = "Pending"
    completedDate: Optional[str] = None
    remarks: Optional[str] = ""

    @validator('status')
    def validate_status(cls, v):
        if v not in ['Pending', 'In Progress', 'Completed']:
            raise ValueError('Status must be Pending, In Progress, or Completed')
        return v

class ActionItemCreate(ActionItemBase):
    pass

class ActionItemUpdate(BaseModel):
    action: Optional[str] = None
    responsible: Optional[str] = None
    targetDate: Optional[str] = None
    status: Optional[str] = None
    completedDate: Optional[str] = None
    remarks: Optional[str] = None

class ActionItemResponse(ActionItemBase):
    id: str
    report_id: str

# Main VFL Report Model
class VFLReportBase(BaseModel):
    observerName: str = Field(..., min_length=1)
    designation: Optional[str] = ""
    sectionChoice: str = Field(..., pattern="^(Mechanical|Electrical)$")
    departmentSection: Optional[str] = ""
    date: str = Field(..., min_length=1)
    time: str = Field(..., min_length=1)
    behaviourCategory: str = Field(..., pattern="^(Safe Behaviour|Unsafe Behaviour)$")
    observationType: str = Field(..., pattern="^(Safe Behaviour|Safe Condition|At Risk Behaviour|At Risk Condition)$")
    description: str = Field(..., min_length=10)
    coachingTechnique: str = Field(..., pattern="^(SBR|CC)$")
    status: str = "draft"

    @validator('status')
    def validate_status(cls, v):
        if v not in ['draft', 'submitted', 'reviewed', 'closed']:
            raise ValueError('Status must be draft, submitted, reviewed, or closed')
        return v

class VFLReportCreate(VFLReportBase):
    actions: List[ActionItemCreate] = []

class VFLReportUpdate(BaseModel):
    observerName: Optional[str] = None
    designation: Optional[str] = None
    sectionChoice: Optional[str] = None
    departmentSection: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    behaviourCategory: Optional[str] = None
    observationType: Optional[str] = None
    description: Optional[str] = None
    coachingTechnique: Optional[str] = None
    status: Optional[str] = None
    actions: Optional[List[ActionItemUpdate]] = None

class VFLReportResponse(VFLReportBase):
    id: str
    actions: List[ActionItemResponse] = []
    created_at: str
    updated_at: Optional[str] = None
    submitted_at: Optional[str] = None

# Stats Models
class VFLStatsResponse(BaseModel):
    total: int
    bySection: Dict[str, int]
    byObserver: Dict[str, int]
    byBehaviour: Dict[str, int]
    byObservationType: Dict[str, int]
    byCoaching: Dict[str, int]
    totalActions: int
    completedActions: int
    pendingActions: int
    inProgressActions: int
    draftCount: int
    submittedCount: int
    reviewedCount: int
    closedCount: int

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_vfl_to_camel(db_item: dict) -> dict:
    """Map database column names (snake_case) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "observerName": db_item.get("observer_name"),
        "designation": db_item.get("designation", ""),
        "sectionChoice": db_item.get("section_choice"),
        "departmentSection": db_item.get("department_section", ""),
        "date": db_item.get("date"),
        "time": db_item.get("time"),
        "behaviourCategory": db_item.get("behaviour_category"),
        "observationType": db_item.get("observation_type"),
        "description": db_item.get("description"),
        "coachingTechnique": db_item.get("coaching_technique"),
        "status": db_item.get("status", "draft"),
        "created_at": db_item.get("created_at"),
        "updated_at": db_item.get("updated_at"),
        "submitted_at": db_item.get("submitted_at")
    }

def map_db_action_to_camel(db_item: dict) -> dict:
    """Map database column names (snake_case) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "report_id": db_item.get("report_id"),
        "action": db_item.get("action"),
        "responsible": db_item.get("responsible"),
        "targetDate": db_item.get("target_date"),
        "status": db_item.get("status", "Pending"),
        "completedDate": db_item.get("completed_date"),
        "remarks": db_item.get("remarks", ""),
        "created_at": db_item.get("created_at")
    }

# =============== API ENDPOINTS ===============

# GET all VFL reports
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_vfl_reports(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    observer: Optional[str] = Query(None, description="Filter by observer name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    behaviour: Optional[str] = Query(None, description="Filter by behaviour category"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    try:
        logger.info("Fetching VFL reports...")
        
        # Start with base query
        query = supabase.table("vfl_reports").select("*")
        
        # Apply filters
        if search:
            query = query.or_(
                f"observer_name.ilike.%{search}%," +
                f"designation.ilike.%{search}%," +
                f"description.ilike.%{search}%," +
                f"department_section.ilike.%{search}%"
            )
        
        if section:
            query = query.eq("section_choice", section)
        
        if observer:
            query = query.ilike("observer_name", f"%{observer}%")
        
        if status:
            query = query.eq("status", status)
        
        if behaviour:
            query = query.eq("behaviour_category", behaviour)
        
        if from_date:
            query = query.gte("date", from_date)
        
        if to_date:
            query = query.lte("date", to_date)
        
        # Order by most recent
        query = query.order("created_at", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()
        
        if hasattr(response, 'data'):
            db_reports = response.data or []
            result = []
            
            # Fetch action items for each report
            for report in db_reports:
                actions_response = supabase.table("vfl_action_plan")\
                    .select("*")\
                    .eq("report_id", report["id"])\
                    .execute()
                
                db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                camel_actions = [map_db_action_to_camel(a) for a in db_actions]
                
                camel_report = map_db_vfl_to_camel(report)
                camel_report["actions"] = camel_actions
                result.append(camel_report)
            
            return result
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching VFL reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# GET unique observers for auto-complete
@router.get("/suggestions/observers", dependencies=[Depends(get_current_user)])
async def get_observer_suggestions(search: Optional[str] = Query(None)):
    try:
        query = supabase.table("vfl_reports")\
            .select("observer_name")\
            .neq("observer_name", "")\
            .not_.is_("observer_name", "null")\
            .order("observer_name")
        
        if search:
            query = query.ilike("observer_name", f"%{search}%")
        
        response = query.execute()
        
        if hasattr(response, 'data'):
            # Get unique observers
            observers = list(set(item["observer_name"] for item in response.data if item.get("observer_name")))
            return sorted(observers)
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching observer suggestions: {str(e)}")
        return []

# GET stats overview
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_vfl_stats():
    try:
        logger.info("Fetching VFL stats...")
        
        # Get all reports
        reports_response = supabase.table("vfl_reports").select("*").execute()
        reports = reports_response.data if hasattr(reports_response, 'data') else []
        
        # Get all actions
        actions_response = supabase.table("vfl_action_plan").select("*").execute()
        actions = actions_response.data if hasattr(actions_response, 'data') else []
        
        # Calculate stats
        total = len(reports)
        
        # Count by section
        by_section = {
            "Mechanical": 0,
            "Electrical": 0
        }
        
        # Count by behaviour
        by_behaviour = {
            "Safe Behaviour": 0,
            "Unsafe Behaviour": 0
        }
        
        # Count by observation type
        by_observation_type = {
            "Safe Behaviour": 0,
            "Safe Condition": 0,
            "At Risk Behaviour": 0,
            "At Risk Condition": 0
        }
        
        # Count by coaching technique
        by_coaching = {
            "SBR": 0,
            "CC": 0
        }
        
        # Count by status
        draft_count = 0
        submitted_count = 0
        reviewed_count = 0
        closed_count = 0
        
        # Count by observer
        by_observer = {}
        
        for report in reports:
            # Section
            section = report.get("section_choice")
            if section in by_section:
                by_section[section] += 1
            
            # Behaviour
            behaviour = report.get("behaviour_category")
            if behaviour in by_behaviour:
                by_behaviour[behaviour] += 1
            
            # Observation type
            obs_type = report.get("observation_type")
            if obs_type in by_observation_type:
                by_observation_type[obs_type] += 1
            
            # Coaching technique
            coaching = report.get("coaching_technique")
            if coaching in by_coaching:
                by_coaching[coaching] += 1
            
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
            
            # Observer
            observer = report.get("observer_name")
            if observer:
                by_observer[observer] = by_observer.get(observer, 0) + 1
        
        # Count actions by status
        pending_actions = 0
        in_progress_actions = 0
        completed_actions = 0
        
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
            "byObserver": by_observer,
            "byBehaviour": by_behaviour,
            "byObservationType": by_observation_type,
            "byCoaching": by_coaching,
            "totalActions": len(actions),
            "pendingActions": pending_actions,
            "inProgressActions": in_progress_actions,
            "completedActions": completed_actions,
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
async def get_vfl_report(report_id: str):
    try:
        logger.info(f"Fetching VFL report {report_id}")
        
        # Get report
        report_response = supabase.table("vfl_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not report_response.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        db_report = report_response.data[0]
        
        # Get action items
        actions_response = supabase.table("vfl_action_plan")\
            .select("*")\
            .eq("report_id", report_id)\
            .execute()
        
        db_actions = actions_response.data if hasattr(actions_response, 'data') else []
        camel_actions = [map_db_action_to_camel(a) for a in db_actions]
        
        camel_report = map_db_vfl_to_camel(db_report)
        camel_report["actions"] = camel_actions
        
        return camel_report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching report: {str(e)}")

# POST create report
@router.post("")
@router.post("/")
async def create_vfl_report(report: VFLReportCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating VFL report for observer: {report.observerName}")
        
        report_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert report - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        report_data = {
            "id": report_id,
            "observer_name": report.observerName,
            "designation": report.designation,
            "section_choice": report.sectionChoice,
            "department_section": report.departmentSection,
            "date": report.date,
            "time": report.time,
            "behaviour_category": report.behaviourCategory,
            "observation_type": report.observationType,
            "description": report.description,
            "coaching_technique": report.coachingTechnique,
            "status": report.status,
            "created_at": now,
            "updated_at": now,
            "submitted_at": now if report.status == "submitted" else None
        }
        
        logger.info(f"Inserting report: {report_data}")
        
        report_response = supabase.table("vfl_reports")\
            .insert(report_data)\
            .execute()
        
        if not report_response.data:
            raise HTTPException(status_code=500, detail="Failed to create report")
        
        created_report = report_response.data[0]
        
        # Insert action items
        if report.actions:
            actions_data = []
            for action in report.actions:
                actions_data.append({
                    "id": generate_id(),
                    "report_id": report_id,
                    "action": action.action,
                    "responsible": action.responsible,
                    "target_date": action.targetDate,
                    "status": action.status,
                    "completed_date": action.completedDate,
                    "remarks": action.remarks,
                    "created_at": now
                })
            
            if actions_data:
                actions_response = supabase.table("vfl_action_plan")\
                    .insert(actions_data)\
                    .execute()
                
                db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                created_report["actions"] = [map_db_action_to_camel(a) for a in db_actions]
            else:
                created_report["actions"] = []
        else:
            created_report["actions"] = []
        
        # Map to camelCase for response
        result = map_db_vfl_to_camel(created_report)
        result["actions"] = created_report.get("actions", [])
        
        logger.info(f"Successfully created VFL report with ID: {report_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating VFL report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# PATCH update report
@router.patch("/{report_id}")
async def update_vfl_report(report_id: str, updated: VFLReportUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating VFL report {report_id}")
        
        # Check if exists
        existing = supabase.table("vfl_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Prepare update data - map camelCase to snake_case for DB
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map fields to snake_case column names
        field_mapping = {
            "observerName": "observer_name",
            "designation": "designation",
            "sectionChoice": "section_choice",
            "departmentSection": "department_section",
            "date": "date",
            "time": "time",
            "behaviourCategory": "behaviour_category",
            "observationType": "observation_type",
            "description": "description",
            "coachingTechnique": "coaching_technique",
            "status": "status"
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None and key != "actions":
                data_to_update[field_mapping[key]] = value
        
        # Always update updated_at timestamp
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        # If status is being set to submitted, set submitted_at
        if "status" in update_dict and update_dict["status"] == "submitted":
            data_to_update["submitted_at"] = datetime.utcnow().isoformat()
        
        if data_to_update:
            update_response = supabase.table("vfl_reports")\
                .update(data_to_update)\
                .eq("id", report_id)\
                .execute()
            
            if not update_response.data:
                raise HTTPException(status_code=500, detail="Update failed")
            
            updated_report = update_response.data[0]
        else:
            updated_report = existing.data[0]
        
        # Update action items if provided
        if updated.actions is not None:
            # Delete existing actions
            supabase.table("vfl_action_plan")\
                .delete()\
                .eq("report_id", report_id)\
                .execute()
            
            # Insert new actions
            if updated.actions:
                actions_data = []
                now = datetime.utcnow().isoformat()
                
                for action in updated.actions:
                    # Only include if it has required fields
                    if action.get("action") and action.get("responsible") and action.get("targetDate"):
                        actions_data.append({
                            "id": generate_id(),
                            "report_id": report_id,
                            "action": action.get("action"),
                            "responsible": action.get("responsible"),
                            "target_date": action.get("targetDate"),
                            "status": action.get("status", "Pending"),
                            "completed_date": action.get("completedDate"),
                            "remarks": action.get("remarks", ""),
                            "created_at": now
                        })
                
                if actions_data:
                    actions_response = supabase.table("vfl_action_plan")\
                        .insert(actions_data)\
                        .execute()
                    
                    db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                    updated_report["actions"] = [map_db_action_to_camel(a) for a in db_actions]
                else:
                    updated_report["actions"] = []
            else:
                updated_report["actions"] = []
        else:
            # Get existing actions
            actions_response = supabase.table("vfl_action_plan")\
                .select("*")\
                .eq("report_id", report_id)\
                .execute()
            
            db_actions = actions_response.data if hasattr(actions_response, 'data') else []
            updated_report["actions"] = [map_db_action_to_camel(a) for a in db_actions]
        
        # Map to camelCase for response
        result = map_db_vfl_to_camel(updated_report)
        result["actions"] = updated_report.get("actions", [])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating report: {str(e)}")

# DELETE report
@router.delete("/{report_id}")
async def delete_vfl_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting VFL report {report_id}")
        
        # Check if exists
        existing = supabase.table("vfl_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Action items will be automatically deleted due to foreign key cascade
        supabase.table("vfl_reports")\
            .delete()\
            .eq("id", report_id)\
            .execute()
        
        return {"success": True, "message": "Report deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting report: {str(e)}")

# Debug endpoint
@router.get("/debug/test")
async def debug_test():
    try:
        # Test select
        select_result = supabase.table("vfl_reports").select("*").execute()
        
        return {
            "status": "success",
            "table_exists": True,
            "current_records": len(select_result.data) if select_result.data else 0,
            "endpoint": "/api/vfl"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }