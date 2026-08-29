# Planned Task Observation (PTO) router
# backend/app/routers/pto.py

# backend/app/routers/pto.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.aggregation import count_by
from app.db_helpers import get_or_404, apply_date_range, or_ilike, distinct_suggestions, status_choice_validator
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/pto", tags=["PTO"])

# =============== PYDANTIC MODELS ===============

# TimeOnJob Model
class TimeOnJob(BaseModel):
    months: str = ""
    years: str = ""

# Notification Model
class Notification(BaseModel):
    toldInAdvance: str = "No"

    @validator('toldInAdvance')
    def validate_told_in_advance(cls, v):
        if v not in ['Yes', 'No']:
            raise ValueError('toldInAdvance must be Yes or No')
        return v

# Reasons Model
class Reasons(BaseModel):
    monthly: bool = False
    newEmployee: bool = False
    safetyAwareness: bool = False
    incidentFollowUp: bool = False
    trainingFollowUp: bool = False
    infrequentTask: bool = False

# Procedures Model
class Procedures(BaseModel):
    hasProcedure: str = "No"
    familiarWithProcedure: str = "No"

    @validator('hasProcedure', 'familiarWithProcedure')
    def validate_procedure_fields(cls, v):
        if v not in ['Yes', 'No']:
            raise ValueError('Field must be Yes or No')
        return v

# RiskAssessment Model
class RiskAssessment(BaseModel):
    made: str = "No"
    identified: str = "No"
    effective: str = "No"

    @validator('made', 'identified', 'effective')
    def validate_risk_fields(cls, v):
        if v not in ['Yes', 'No']:
            raise ValueError('Field must be Yes or No')
        return v

# SuggestedRemedies Model
class SuggestedRemedies(BaseModel):
    newProcedure: str = "No"
    reviseExisting: str = "No"
    differentEquipment: str = "No"
    engineeringControls: str = "No"
    retraining: str = "No"
    improvedPPE: str = "No"
    placementOfWorker: str = "No"

    @validator('newProcedure', 'reviseExisting', 'differentEquipment', 
               'engineeringControls', 'retraining', 'improvedPPE', 'placementOfWorker')
    def validate_remedy_fields(cls, v):
        if v not in ['Yes', 'No']:
            raise ValueError('Field must be Yes or No')
        return v

# Action Plan Item
class ActionPlanItemBase(BaseModel):
    action: str = Field(..., min_length=1)
    byWhom: str = Field(..., min_length=1)
    byWhen: str = Field(..., min_length=1)
    status: str = "Pending"
    completedDate: Optional[str] = None
    remarks: Optional[str] = ""

    _validate_status = validator('status')(status_choice_validator(
        ['Pending', 'In Progress', 'Completed'], 'Status must be Pending, In Progress, or Completed'))

class ActionPlanItemCreate(ActionPlanItemBase):
    no: int

class ActionPlanItemUpdate(BaseModel):
    action: Optional[str] = None
    byWhom: Optional[str] = None
    byWhen: Optional[str] = None
    status: Optional[str] = None
    completedDate: Optional[str] = None
    remarks: Optional[str] = None

class ActionPlanItemResponse(ActionPlanItemBase):
    id: str
    no: int
    report_id: str

# Main PTO Report Model
class PTOReportBase(BaseModel):
    date: str = Field(..., min_length=1)
    observerName: str = Field(..., min_length=1)
    section: str = Field(..., pattern="^(Mechanical|Electrical)$")
    deptSectionContractor: Optional[str] = ""
    workerName: str = Field(..., min_length=1)
    occupation: Optional[str] = ""
    jobTaskObserved: str = Field(..., min_length=1)
    sheqRefNo: Optional[str] = ""
    observationType: str = Field(..., pattern="^(Initial|Follow up)$")
    timeOnJob: TimeOnJob
    notification: Notification
    reasons: Reasons
    procedures: Procedures
    riskAssessment: RiskAssessment
    suggestedRemedies: SuggestedRemedies
    observationScope: str = Field(..., pattern="^(All|Partial)$")
    followUpNeeded: str = Field(..., pattern="^(Yes|No)$")
    status: str = "draft"

    _validate_status = validator('status')(status_choice_validator(
        ['draft', 'submitted', 'reviewed', 'closed'], 'Status must be draft, submitted, reviewed, or closed'))

class PTOReportCreate(PTOReportBase):
    actionPlan: List[ActionPlanItemCreate] = []

class PTOReportUpdate(BaseModel):
    date: Optional[str] = None
    observerName: Optional[str] = None
    section: Optional[str] = None
    deptSectionContractor: Optional[str] = None
    workerName: Optional[str] = None
    occupation: Optional[str] = None
    jobTaskObserved: Optional[str] = None
    sheqRefNo: Optional[str] = None
    observationType: Optional[str] = None
    timeOnJob: Optional[TimeOnJob] = None
    notification: Optional[Notification] = None
    reasons: Optional[Reasons] = None
    procedures: Optional[Procedures] = None
    riskAssessment: Optional[RiskAssessment] = None
    suggestedRemedies: Optional[SuggestedRemedies] = None
    observationScope: Optional[str] = None
    followUpNeeded: Optional[str] = None
    status: Optional[str] = None
    actionPlan: Optional[List[ActionPlanItemUpdate]] = None

class PTOReportResponse(PTOReportBase):
    id: str
    actionPlan: List[ActionPlanItemResponse] = []
    created_at: str
    updated_at: Optional[str] = None
    submitted_at: Optional[str] = None

# Stats Models
class PTOStatsResponse(BaseModel):
    total: int
    bySection: Dict[str, int]
    byObserver: Dict[str, int]
    totalActions: int
    completedActions: int
    pendingActions: int
    inProgressActions: int
    highRiskCount: int
    initialObservations: int
    followUpObservations: int
    draftCount: int
    submittedCount: int
    reviewedCount: int
    closedCount: int

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_pto_to_camel(db_item: dict) -> dict:
    """Map database column names (snake_case) to camelCase for frontend"""
    return {
        "id": db_item.get("id"),
        "date": db_item.get("date"),
        "observerName": db_item.get("observer_name"),
        "section": db_item.get("section"),
        "deptSectionContractor": db_item.get("dept_section_contractor", ""),
        "workerName": db_item.get("worker_name"),
        "occupation": db_item.get("occupation", ""),
        "jobTaskObserved": db_item.get("job_task_observed"),
        "sheqRefNo": db_item.get("sheq_ref_no", ""),
        "observationType": db_item.get("observation_type"),
        "timeOnJob": db_item.get("time_on_job", {"months": "", "years": ""}),
        "notification": db_item.get("notification", {"toldInAdvance": "No"}),
        "reasons": db_item.get("reasons", {
            "monthly": False,
            "newEmployee": False,
            "safetyAwareness": False,
            "incidentFollowUp": False,
            "trainingFollowUp": False,
            "infrequentTask": False
        }),
        "procedures": db_item.get("procedures", {
            "hasProcedure": "No",
            "familiarWithProcedure": "No"
        }),
        "riskAssessment": db_item.get("risk_assessment", {
            "made": "No",
            "identified": "No",
            "effective": "No"
        }),
        "suggestedRemedies": db_item.get("suggested_remedies", {
            "newProcedure": "No",
            "reviseExisting": "No",
            "differentEquipment": "No",
            "engineeringControls": "No",
            "retraining": "No",
            "improvedPPE": "No",
            "placementOfWorker": "No"
        }),
        "observationScope": db_item.get("observation_scope"),
        "followUpNeeded": db_item.get("follow_up_needed"),
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
        "no": db_item.get("no"),
        "action": db_item.get("action"),
        "byWhom": db_item.get("by_whom"),
        "byWhen": db_item.get("by_when"),
        "status": db_item.get("status", "Pending"),
        "completedDate": db_item.get("completed_date"),
        "remarks": db_item.get("remarks", ""),
        "created_at": db_item.get("created_at")
    }

# =============== API ENDPOINTS ===============

# GET all PTO reports
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_pto_reports(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    observer: Optional[str] = Query(None, description="Filter by observer name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    try:
        logger.info("Fetching PTO reports...")
        
        # Start with base query
        query = supabase.table("pto_reports").select("*")
        
        # Apply filters
        if search:
            query = query.or_(or_ilike(["observer_name", "worker_name", "job_task_observed"], search))

        if section:
            query = query.eq("section", section)

        if observer:
            query = query.ilike("observer_name", f"%{observer}%")

        if status:
            query = query.eq("status", status)

        query = apply_date_range(query, "date", from_date, to_date)

        # Order by most recent
        query = query.order("created_at", desc=True)
        query = query.range(offset, offset + limit - 1)
        
        response = query.execute()
        
        if hasattr(response, 'data'):
            db_reports = response.data or []
            result = []
            
            # Fetch action plan items for each report
            for report in db_reports:
                actions_response = supabase.table("pto_action_plan")\
                    .select("*")\
                    .eq("report_id", report["id"])\
                    .order("no")\
                    .execute()
                
                db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                camel_actions = [map_db_action_to_camel(a) for a in db_actions]
                
                camel_report = map_db_pto_to_camel(report)
                camel_report["actionPlan"] = camel_actions
                result.append(camel_report)
            
            return result
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching PTO reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# GET unique observers for auto-complete
@router.get("/suggestions/observers", dependencies=[Depends(get_current_user)])
async def get_observer_suggestions(search: Optional[str] = Query(None)):
    return await distinct_suggestions(supabase, "pto_reports", "observer_name", search, "observer")

# GET stats overview
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_pto_stats():
    try:
        logger.info("Fetching PTO stats...")
        
        # Get all reports
        reports_response = supabase.table("pto_reports").select("*").execute()
        reports = reports_response.data if hasattr(reports_response, 'data') else []
        
        # Get all actions
        actions_response = supabase.table("pto_action_plan").select("*").execute()
        actions = actions_response.data if hasattr(actions_response, 'data') else []
        
        # Calculate stats
        total = len(reports)
        
        # Count by section
        by_section = {
            "Mechanical": 0,
            "Electrical": 0
        }
        
        # Count by status
        draft_count = 0
        submitted_count = 0
        reviewed_count = 0
        closed_count = 0
        
        # Count by observer
        by_observer = count_by((r.get("observer_name") for r in reports if r.get("observer_name")), lambda name: name)

        # Count by observation type
        initial_count = 0
        follow_up_count = 0
        
        # Count high risk
        high_risk_count = 0
        
        for report in reports:
            # Section
            section = report.get("section")
            if section in by_section:
                by_section[section] += 1
            
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
            
            # Observation type
            obs_type = report.get("observation_type")
            if obs_type == "Initial":
                initial_count += 1
            elif obs_type == "Follow up":
                follow_up_count += 1
            
            # High risk check
            risk = report.get("risk_assessment", {})
            if risk.get("made") == "No" or risk.get("identified") == "No" or risk.get("effective") == "No":
                high_risk_count += 1
        
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
            "totalActions": len(actions),
            "pendingActions": pending_actions,
            "inProgressActions": in_progress_actions,
            "completedActions": completed_actions,
            "highRiskCount": high_risk_count,
            "initialObservations": initial_count,
            "followUpObservations": follow_up_count,
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
async def get_pto_report(report_id: str):
    try:
        logger.info(f"Fetching PTO report {report_id}")
        
        # Get report
        report_response = supabase.table("pto_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not report_response.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        db_report = report_response.data[0]
        
        # Get action plan items
        actions_response = supabase.table("pto_action_plan")\
            .select("*")\
            .eq("report_id", report_id)\
            .order("no")\
            .execute()
        
        db_actions = actions_response.data if hasattr(actions_response, 'data') else []
        camel_actions = [map_db_action_to_camel(a) for a in db_actions]
        
        camel_report = map_db_pto_to_camel(db_report)
        camel_report["actionPlan"] = camel_actions
        
        return camel_report
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching report: {str(e)}")

# POST create report
@router.post("")
@router.post("/")
async def create_pto_report(report: PTOReportCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating PTO report for observer: {report.observerName}")
        
        report_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert report - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        report_data = {
            "id": report_id,
            "date": report.date,
            "observer_name": report.observerName,
            "section": report.section,
            "dept_section_contractor": report.deptSectionContractor,
            "worker_name": report.workerName,
            "occupation": report.occupation,
            "job_task_observed": report.jobTaskObserved,
            "sheq_ref_no": report.sheqRefNo,
            "observation_type": report.observationType,
            "time_on_job": report.timeOnJob.dict(),
            "notification": report.notification.dict(),
            "reasons": report.reasons.dict(),
            "procedures": report.procedures.dict(),
            "risk_assessment": report.riskAssessment.dict(),
            "suggested_remedies": report.suggestedRemedies.dict(),
            "observation_scope": report.observationScope,
            "follow_up_needed": report.followUpNeeded,
            "status": report.status,
            "created_at": now,
            "updated_at": now,
            "submitted_at": now if report.status == "submitted" else None
        }
        
        logger.info(f"Inserting report: {report_data}")
        
        report_response = supabase.table("pto_reports")\
            .insert(report_data)\
            .execute()
        
        if not report_response.data:
            raise HTTPException(status_code=500, detail="Failed to create report")
        
        created_report = report_response.data[0]
        
        # Insert action plan items
        if report.actionPlan:
            actions_data = []
            for action in report.actionPlan:
                actions_data.append({
                    "id": generate_id(),
                    "report_id": report_id,
                    "no": action.no,
                    "action": action.action,
                    "by_whom": action.byWhom,
                    "by_when": action.byWhen,
                    "status": action.status,
                    "completed_date": action.completedDate,
                    "remarks": action.remarks,
                    "created_at": now
                })
            
            if actions_data:
                actions_response = supabase.table("pto_action_plan")\
                    .insert(actions_data)\
                    .execute()
                
                db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                created_report["actionPlan"] = [map_db_action_to_camel(a) for a in db_actions]
            else:
                created_report["actionPlan"] = []
        else:
            created_report["actionPlan"] = []
        
        # Map to camelCase for response
        result = map_db_pto_to_camel(created_report)
        result["actionPlan"] = created_report.get("actionPlan", [])
        
        logger.info(f"Successfully created PTO report with ID: {report_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating PTO report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# PATCH update report
@router.patch("/{report_id}")
async def update_pto_report(report_id: str, updated: PTOReportUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating PTO report {report_id}")
        
        # Check if exists
        existing = supabase.table("pto_reports")\
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
            "date": "date",
            "observerName": "observer_name",
            "section": "section",
            "deptSectionContractor": "dept_section_contractor",
            "workerName": "worker_name",
            "occupation": "occupation",
            "jobTaskObserved": "job_task_observed",
            "sheqRefNo": "sheq_ref_no",
            "observationType": "observation_type",
            "timeOnJob": "time_on_job",
            "notification": "notification",
            "reasons": "reasons",
            "procedures": "procedures",
            "riskAssessment": "risk_assessment",
            "suggestedRemedies": "suggested_remedies",
            "observationScope": "observation_scope",
            "followUpNeeded": "follow_up_needed",
            "status": "status"
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None and key != "actionPlan":
                # Convert Pydantic models to dict
                if hasattr(value, 'dict'):
                    data_to_update[field_mapping[key]] = value.dict()
                else:
                    data_to_update[field_mapping[key]] = value
        
        # Always update updated_at timestamp
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        # If status is being set to submitted, set submitted_at
        if "status" in update_dict and update_dict["status"] == "submitted":
            data_to_update["submitted_at"] = datetime.utcnow().isoformat()
        
        if data_to_update:
            update_response = supabase.table("pto_reports")\
                .update(data_to_update)\
                .eq("id", report_id)\
                .execute()
            
            if not update_response.data:
                raise HTTPException(status_code=500, detail="Update failed")
            
            updated_report = update_response.data[0]
        else:
            updated_report = existing.data[0]
        
        # Update action plan items if provided
        if updated.actionPlan is not None:
            # Delete existing actions
            supabase.table("pto_action_plan")\
                .delete()\
                .eq("report_id", report_id)\
                .execute()
            
            # Insert new actions
            if updated.actionPlan:
                actions_data = []
                now = datetime.utcnow().isoformat()
                
                for i, action in enumerate(updated.actionPlan, start=1):
                    # `action` is an ActionPlanItemUpdate model instance, not a dict —
                    # .get(...) on it raised AttributeError at runtime on every PATCH
                    # that included an actionPlan array (real, live bug; found while
                    # investigating this file's pyright errors, not just type noise).
                    if action.action and action.byWhom and action.byWhen:
                        actions_data.append({
                            "id": generate_id(),
                            "report_id": report_id,
                            "no": i,
                            "action": action.action,
                            "by_whom": action.byWhom,
                            "by_when": action.byWhen,
                            "status": action.status or "Pending",
                            "completed_date": action.completedDate,
                            "remarks": action.remarks or "",
                            "created_at": now
                        })
                
                if actions_data:
                    actions_response = supabase.table("pto_action_plan")\
                        .insert(actions_data)\
                        .execute()
                    
                    db_actions = actions_response.data if hasattr(actions_response, 'data') else []
                    updated_report["actionPlan"] = [map_db_action_to_camel(a) for a in db_actions]
                else:
                    updated_report["actionPlan"] = []
            else:
                updated_report["actionPlan"] = []
        else:
            # Get existing actions
            actions_response = supabase.table("pto_action_plan")\
                .select("*")\
                .eq("report_id", report_id)\
                .order("no")\
                .execute()
            
            db_actions = actions_response.data if hasattr(actions_response, 'data') else []
            updated_report["actionPlan"] = [map_db_action_to_camel(a) for a in db_actions]
        
        # Map to camelCase for response
        result = map_db_pto_to_camel(updated_report)
        result["actionPlan"] = updated_report.get("actionPlan", [])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating report: {str(e)}")

# DELETE report
@router.delete("/{report_id}")
async def delete_pto_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting PTO report {report_id}")
        
        # Check if exists
        existing = supabase.table("pto_reports")\
            .select("*")\
            .eq("id", report_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Action plan items will be automatically deleted due to foreign key cascade
        supabase.table("pto_reports")\
            .delete()\
            .eq("id", report_id)\
            .execute()
        
        return {"success": True, "message": "Report deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting report: {str(e)}")
