# backend/app/routers/sheq_inspections.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sheq", tags=["SHEQ Inspections"])

# =============== PYDANTIC MODELS ===============

class FindingBase(BaseModel):
    finding: str = Field(..., min_length=1)
    requiredAction: str = Field(..., min_length=1)
    byWho: str = Field(..., min_length=1)
    byWhen: str = Field(..., min_length=1)
    status: str = "open"
    priority: str = "medium"
    section: str
    completedDate: Optional[str] = None
    remarks: Optional[str] = None

class FindingCreate(FindingBase):
    pass

class FindingUpdate(BaseModel):
    finding: Optional[str] = None
    requiredAction: Optional[str] = None
    byWho: Optional[str] = None
    byWhen: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    section: Optional[str] = None
    completedDate: Optional[str] = None
    remarks: Optional[str] = None

class SHEQBase(BaseModel):
    inspectors: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    place: str = Field(..., min_length=1)
    date: str = Field(..., min_length=1)
    time: str = Field(..., min_length=1)
    department: Optional[str] = ""
    section: str
    hodName: Optional[str] = ""
    sheqOfficialName: Optional[str] = ""
    hodSignature: Optional[str] = None
    sheqSignature: Optional[str] = None
    status: str = "draft"
    before_photos: List[str] = []
    after_photos: List[str] = []

class SHEQCreate(SHEQBase):
    findings: List[FindingCreate] = []

class SHEQUpdate(BaseModel):
    inspectors: Optional[str] = None
    title: Optional[str] = None
    place: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    department: Optional[str] = None
    section: Optional[str] = None
    hodName: Optional[str] = None
    sheqOfficialName: Optional[str] = None
    hodSignature: Optional[str] = None
    sheqSignature: Optional[str] = None
    status: Optional[str] = None
    findings: Optional[List[FindingUpdate]] = None
    before_photos: Optional[List[str]] = None
    after_photos: Optional[List[str]] = None

# =============== HELPER FUNCTIONS ===============

def generate_id():
    return str(uuid.uuid4())

def map_db_inspection_to_camel(db_inspection: dict) -> dict:
    """Map database column names (lowercase) to camelCase for frontend"""
    return {
        "id": db_inspection.get("id"),
        "inspectors": db_inspection.get("inspectors"),
        "title": db_inspection.get("title"),
        "place": db_inspection.get("place"),
        "date": db_inspection.get("date"),
        "time": db_inspection.get("time"),
        "department": db_inspection.get("department", ""),
        "section": db_inspection.get("section"),
        "hodName": db_inspection.get("hodname", ""),
        "sheqOfficialName": db_inspection.get("sheqofficialname", ""),
        "hodSignature": db_inspection.get("hodsignature"),
        "sheqSignature": db_inspection.get("sheqsignature"),
        "status": db_inspection.get("status", "draft"),
        "before_photos": db_inspection.get("before_photos") or [],
        "after_photos": db_inspection.get("after_photos") or [],
        "createdAt": db_inspection.get("created_at"),
        "updatedAt": db_inspection.get("updated_at")
    }

def map_db_finding_to_camel(db_finding: dict) -> dict:
    """Map database column names (lowercase) to camelCase for frontend"""
    return {
        "id": db_finding.get("id"),
        "finding": db_finding.get("finding"),
        "requiredAction": db_finding.get("requiredaction"),
        "byWho": db_finding.get("bywho"),
        "byWhen": db_finding.get("bywhen"),
        "status": db_finding.get("status"),
        "priority": db_finding.get("priority"),
        "section": db_finding.get("section"),
        "completedDate": db_finding.get("completeddate"),
        "remarks": db_finding.get("remarks")
    }

# =============== API ENDPOINTS ===============

# GET all inspections
@router.get("", dependencies=[Depends(get_current_user)])
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_inspections(
    search: Optional[str] = Query(None, description="Search term"),
    section: Optional[str] = Query(None, description="Filter by section"),
    status: Optional[str] = Query(None, description="Filter by status"),
    inspector: Optional[str] = Query(None, description="Filter by inspector"),
    from_date: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD")
):
    try:
        logger.info("Fetching SHEQ inspections...")
        
        # Start with base query
        query = supabase.table("sheq_inspections").select("*")
        
        # Apply filters
        if search:
            query = query.or_(
                f"title.ilike.%{search}%," +
                f"inspectors.ilike.%{search}%," +
                f"place.ilike.%{search}%"
            )
        
        if section:
            query = query.eq("section", section)
        
        if status:
            query = query.eq("status", status)
        
        if inspector:
            query = query.ilike("inspectors", f"%{inspector}%")
        
        if from_date:
            query = query.gte("date", from_date)
        
        if to_date:
            query = query.lte("date", to_date)
        
        # Order by most recent
        query = query.order("created_at", desc=True)
        
        response = query.execute()
        
        logger.info(f"Supabase response: {response}")
        
        if hasattr(response, 'data'):
            inspections = response.data or []
            result = []
            
            # Fetch findings for each inspection and map to camelCase
            for inspection in inspections:
                findings_response = supabase.table("sheq_findings")\
                    .select("*")\
                    .eq("inspection_id", inspection["id"])\
                    .execute()
                
                db_findings = findings_response.data if hasattr(findings_response, 'data') else []
                camel_findings = [map_db_finding_to_camel(f) for f in db_findings]
                
                camel_inspection = map_db_inspection_to_camel(inspection)
                camel_inspection["findings"] = camel_findings
                result.append(camel_inspection)
            
            return result
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching inspections: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching inspections: {str(e)}")

# GET stats overview
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_inspection_stats():
    try:
        logger.info("Fetching inspection stats...")
        
        # Get all inspections
        inspections_response = supabase.table("sheq_inspections").select("*").execute()
        inspections = inspections_response.data if hasattr(inspections_response, 'data') else []
        
        # Get all findings
        findings_response = supabase.table("sheq_findings").select("*").execute()
        findings = findings_response.data if hasattr(findings_response, 'data') else []
        
        # Calculate stats
        stats = {
            "total": len(inspections),
            "open": 0,
            "inProgress": 0,
            "closed": 0,
            "overdue": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "bySection": {
                "mechanical": 0,
                "electrical": 0
            },
            "byInspector": {}
        }
        
        # Count by section
        for inspection in inspections:
            section = inspection.get("section")
            if section in stats["bySection"]:
                stats["bySection"][section] += 1
            
            # Count by inspector
            inspectors_list = inspection.get("inspectors", "").split(",")
            for inspector in inspectors_list:
                name = inspector.strip()
                if name:
                    stats["byInspector"][name] = stats["byInspector"].get(name, 0) + 1
        
        # Count findings by status and priority
        for finding in findings:
            status = finding.get("status")
            if status == "open":
                stats["open"] += 1
            elif status == "in-progress":
                stats["inProgress"] += 1
            elif status == "closed":
                stats["closed"] += 1
            elif status == "overdue":
                stats["overdue"] += 1
            
            priority = finding.get("priority")
            if priority == "critical":
                stats["critical"] += 1
            elif priority == "high":
                stats["high"] += 1
            elif priority == "medium":
                stats["medium"] += 1
            elif priority == "low":
                stats["low"] += 1
        
        return stats
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching stats: {str(e)}")

# GET single inspection
@router.get("/{inspection_id}", dependencies=[Depends(get_current_user)])
async def get_inspection(inspection_id: str):
    try:
        logger.info(f"Fetching inspection {inspection_id}")
        
        # Get inspection
        inspection_response = supabase.table("sheq_inspections")\
            .select("*")\
            .eq("id", inspection_id)\
            .execute()
        
        if not inspection_response.data:
            raise HTTPException(status_code=404, detail="Inspection not found")
        
        db_inspection = inspection_response.data[0]
        
        # Get findings
        findings_response = supabase.table("sheq_findings")\
            .select("*")\
            .eq("inspection_id", inspection_id)\
            .execute()
        
        db_findings = findings_response.data if hasattr(findings_response, 'data') else []
        camel_findings = [map_db_finding_to_camel(f) for f in db_findings]
        
        camel_inspection = map_db_inspection_to_camel(db_inspection)
        camel_inspection["findings"] = camel_findings
        
        return camel_inspection
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching inspection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching inspection: {str(e)}")

# POST create inspection
@router.post("")
@router.post("/")
async def create_inspection(inspection: SHEQCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Creating inspection: {inspection.title}")
        
        inspection_id = generate_id()
        now = datetime.utcnow().isoformat()
        
        # Insert inspection - USING LOWERCASE COLUMN NAMES TO MATCH DATABASE
        inspection_data = {
            "id": inspection_id,
            "inspectors": inspection.inspectors,
            "title": inspection.title,
            "place": inspection.place,
            "date": inspection.date,
            "time": inspection.time,
            "department": inspection.department,
            "section": inspection.section,
            "hodname": inspection.hodName,              # lowercase to match DB
            "sheqofficialname": inspection.sheqOfficialName,  # lowercase to match DB
            "hodsignature": inspection.hodSignature,     # lowercase to match DB
            "sheqsignature": inspection.sheqSignature,   # lowercase to match DB
            "status": inspection.status,
            "before_photos": inspection.before_photos,
            "after_photos": inspection.after_photos,
            "created_at": now,
            "updated_at": now
        }
        
        logger.info(f"Inserting inspection: {inspection_data}")
        
        inspection_response = supabase.table("sheq_inspections")\
            .insert(inspection_data)\
            .execute()
        
        if not inspection_response.data:
            raise HTTPException(status_code=500, detail="Failed to create inspection")
        
        created_inspection = inspection_response.data[0]
        
        # Insert findings - WITH LOWERCASE COLUMN NAMES
        if inspection.findings:
            findings_data = []
            for finding in inspection.findings:
                findings_data.append({
                    "id": generate_id(),
                    "inspection_id": inspection_id,
                    "finding": finding.finding,
                    "requiredaction": finding.requiredAction,  # lowercase
                    "bywho": finding.byWho,                    # lowercase
                    "bywhen": finding.byWhen,                  # lowercase
                    "status": finding.status,
                    "priority": finding.priority,
                    "section": finding.section,
                    "completeddate": finding.completedDate,    # lowercase
                    "remarks": finding.remarks
                })
            
            if findings_data:
                findings_response = supabase.table("sheq_findings")\
                    .insert(findings_data)\
                    .execute()
                
                db_findings = findings_response.data if hasattr(findings_response, 'data') else []
                created_inspection["findings"] = [map_db_finding_to_camel(f) for f in db_findings]
            else:
                created_inspection["findings"] = []
        else:
            created_inspection["findings"] = []
        
        # Map the created inspection to camelCase for response
        result = map_db_inspection_to_camel(created_inspection)
        result["findings"] = created_inspection.get("findings", [])
        
        logger.info(f"Successfully created inspection with ID: {inspection_id}")
        return result
        
    except Exception as e:
        logger.error(f"Error creating inspection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating inspection: {str(e)}")

# PATCH update inspection
@router.patch("/{inspection_id}")
async def update_inspection(inspection_id: str, updated: SHEQUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating inspection {inspection_id}")
        
        # Check if exists
        existing = supabase.table("sheq_inspections")\
            .select("*")\
            .eq("id", inspection_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Inspection not found")
        
        # Prepare update data - map camelCase to lowercase for DB
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map fields to lowercase column names
        field_mapping = {
            "inspectors": "inspectors",
            "title": "title",
            "place": "place",
            "date": "date",
            "time": "time",
            "department": "department",
            "section": "section",
            "hodName": "hodname",
            "sheqOfficialName": "sheqofficialname",
            "hodSignature": "hodsignature",
            "sheqSignature": "sheqsignature",
            "status": "status",
            "before_photos": "before_photos",
            "after_photos": "after_photos",
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None and key != "findings":
                data_to_update[field_mapping[key]] = value
            # lists can be empty — treat separately so [] clears photos
            elif key in ("before_photos", "after_photos") and isinstance(value, list):
                data_to_update[key] = value
        
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        # Update inspection
        if data_to_update:
            update_response = supabase.table("sheq_inspections")\
                .update(data_to_update)\
                .eq("id", inspection_id)\
                .execute()
            
            if not update_response.data:
                raise HTTPException(status_code=500, detail="Update failed")
            
            updated_inspection = update_response.data[0]
        else:
            updated_inspection = existing.data[0]
        
        # Update findings if provided
        if updated.findings is not None:
            # Delete existing findings
            supabase.table("sheq_findings")\
                .delete()\
                .eq("inspection_id", inspection_id)\
                .execute()
            
            # Insert new findings
            if updated.findings:
                findings_data = []
                for finding in updated.findings:
                    findings_data.append({
                        "id": generate_id(),
                        "inspection_id": inspection_id,
                        "finding": finding.finding,
                        "requiredaction": finding.requiredAction,  # lowercase
                        "bywho": finding.byWho,                    # lowercase
                        "bywhen": finding.byWhen,                  # lowercase
                        "status": finding.status,
                        "priority": finding.priority,
                        "section": finding.section,
                        "completeddate": finding.completedDate,    # lowercase
                        "remarks": finding.remarks
                    })
                
                findings_response = supabase.table("sheq_findings")\
                    .insert(findings_data)\
                    .execute()
                
                db_findings = findings_response.data if hasattr(findings_response, 'data') else []
                updated_inspection["findings"] = [map_db_finding_to_camel(f) for f in db_findings]
            else:
                updated_inspection["findings"] = []
        else:
            # Get existing findings
            findings_response = supabase.table("sheq_findings")\
                .select("*")\
                .eq("inspection_id", inspection_id)\
                .execute()
            
            db_findings = findings_response.data if hasattr(findings_response, 'data') else []
            updated_inspection["findings"] = [map_db_finding_to_camel(f) for f in db_findings]
        
        # Map to camelCase for response
        result = map_db_inspection_to_camel(updated_inspection)
        result["findings"] = updated_inspection.get("findings", [])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating inspection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating inspection: {str(e)}")

# DELETE inspection
@router.delete("/{inspection_id}")
async def delete_inspection(inspection_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting inspection {inspection_id}")
        
        # Check if exists
        existing = supabase.table("sheq_inspections")\
            .select("*")\
            .eq("id", inspection_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Inspection not found")
        
        # Findings will be automatically deleted due to foreign key cascade
        supabase.table("sheq_inspections")\
            .delete()\
            .eq("id", inspection_id)\
            .execute()
        
        return {"success": True, "message": "Inspection deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting inspection: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting inspection: {str(e)}")

# GET findings for an inspection
@router.get("/{inspection_id}/findings", dependencies=[Depends(get_current_user)])
async def get_findings(inspection_id: str):
    try:
        logger.info(f"Fetching findings for inspection {inspection_id}")
        
        response = supabase.table("sheq_findings")\
            .select("*")\
            .eq("inspection_id", inspection_id)\
            .execute()
        
        db_findings = response.data if hasattr(response, 'data') else []
        return [map_db_finding_to_camel(f) for f in db_findings]
        
    except Exception as e:
        logger.error(f"Error fetching findings: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching findings: {str(e)}")

# POST add finding to inspection
@router.post("/{inspection_id}/findings")
async def add_finding(inspection_id: str, finding: FindingCreate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Adding finding to inspection {inspection_id}")
        
        # Check if inspection exists
        inspection = supabase.table("sheq_inspections")\
            .select("*")\
            .eq("id", inspection_id)\
            .execute()
        
        if not inspection.data:
            raise HTTPException(status_code=404, detail="Inspection not found")
        
        finding_data = {
            "id": generate_id(),
            "inspection_id": inspection_id,
            "finding": finding.finding,
            "requiredaction": finding.requiredAction,  # lowercase
            "bywho": finding.byWho,                    # lowercase
            "bywhen": finding.byWhen,                  # lowercase
            "status": finding.status,
            "priority": finding.priority,
            "section": finding.section,
            "completeddate": finding.completedDate,    # lowercase
            "remarks": finding.remarks
        }
        
        response = supabase.table("sheq_findings")\
            .insert(finding_data)\
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to add finding")
        
        db_finding = response.data[0]
        return map_db_finding_to_camel(db_finding)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding finding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error adding finding: {str(e)}")

# PATCH update finding
@router.patch("/findings/{finding_id}")
async def update_finding(finding_id: str, updated: FindingUpdate, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Updating finding {finding_id}")
        
        # Check if exists
        existing = supabase.table("sheq_findings")\
            .select("*")\
            .eq("id", finding_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Finding not found")
        
        # Prepare update data
        data_to_update = {}
        update_dict = updated.dict(exclude_unset=True)
        
        # Map to lowercase column names
        field_mapping = {
            "finding": "finding",
            "requiredAction": "requiredaction",
            "byWho": "bywho",
            "byWhen": "bywhen",
            "status": "status",
            "priority": "priority",
            "section": "section",
            "completedDate": "completeddate",
            "remarks": "remarks"
        }
        
        for key, value in update_dict.items():
            if key in field_mapping and value is not None:
                data_to_update[field_mapping[key]] = value
        
        if not data_to_update:
            return map_db_finding_to_camel(existing.data[0])
        
        response = supabase.table("sheq_findings")\
            .update(data_to_update)\
            .eq("id", finding_id)\
            .execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Update failed")
        
        db_finding = response.data[0]
        return map_db_finding_to_camel(db_finding)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating finding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating finding: {str(e)}")

# DELETE finding
@router.delete("/findings/{finding_id}")
async def delete_finding(finding_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        logger.info(f"Deleting finding {finding_id}")
        
        # Check if exists
        existing = supabase.table("sheq_findings")\
            .select("*")\
            .eq("id", finding_id)\
            .execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Finding not found")
        
        supabase.table("sheq_findings")\
            .delete()\
            .eq("id", finding_id)\
            .execute()
        
        return {"success": True, "message": "Finding deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting finding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting finding: {str(e)}")

# Debug endpoint to test connection
@router.get("/debug/test")
async def debug_test():
    try:
        # Test select
        select_result = supabase.table("sheq_inspections").select("*").execute()
        
        # Test insert
        test_id = generate_id()
        now = datetime.utcnow().isoformat()
        test_data = {
            "id": test_id,
            "inspectors": "Debug Tester",
            "title": "Debug Test Inspection",
            "place": "Test Location",
            "date": "2024-01-15",
            "time": "10:00",
            "department": "Testing",
            "section": "mechanical",
            "hodname": "Test HOD",              # lowercase
            "sheqofficialname": "Test SHEQ",     # lowercase
            "status": "draft",
            "created_at": now,
            "updated_at": now
        }
        
        insert_result = supabase.table("sheq_inspections").insert(test_data).execute()
        
        # Clean up test data
        if insert_result.data:
            supabase.table("sheq_inspections").delete().eq("id", test_id).execute()
        
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