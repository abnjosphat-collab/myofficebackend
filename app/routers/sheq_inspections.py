# backend/app/routers/sheq_inspections.py

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from app.supabase_client import supabase, rows, one_row
from app.auth import get_current_user, require_role
from app.aggregation import count_by
from app.db_helpers import get_or_404, apply_date_range, or_ilike
from app.scope import apply_list_scope, assert_row_visible, stamp_create, viewer_scope
from app.errors import server_error
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sheq", tags=["SHEQ Inspections"])

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
    to_date: Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    current_user: dict = Depends(get_current_user)
):
    try:
        logger.info("Fetching SHEQ inspections...")
        
        # Start with base query
        query = supabase.table("sheq_inspections").select("*")
        
        # Apply filters
        if search:
            query = query.or_(or_ilike(["title", "inspectors", "place"], search))

        if section:
            query = query.eq("section", section)

        if status:
            query = query.eq("status", status)

        if inspector:
            query = query.ilike("inspectors", f"%{inspector}%")

        query = apply_date_range(query, "date", from_date, to_date)
        query = apply_list_scope(query, current_user, "sheq_inspections")

        # Order by most recent
        query = query.order("created_at", desc=True)
        
        response = query.execute()
        
        logger.info(f"Supabase response: {response}")
        
        if hasattr(response, 'data'):
            inspections = rows(response)
            result = []

            # Fetch findings for each inspection and map to camelCase
            for inspection in inspections:
                findings_response = supabase.table("sheq_findings")\
                    .select("*")\
                    .eq("inspection_id", inspection["id"])\
                    .execute()

                db_findings = rows(findings_response)
                camel_findings = [map_db_finding_to_camel(f) for f in db_findings]
                
                camel_inspection = map_db_inspection_to_camel(inspection)
                camel_inspection["findings"] = camel_findings
                result.append(camel_inspection)
            
            return result
        
        return []
        
    except Exception as e:
        logger.error(f"Error fetching inspections: {str(e)}")
        raise server_error(e, "Loading inspections")

# GET stats overview
@router.get("/stats/overview", dependencies=[Depends(get_current_user)])
async def get_inspection_stats(current_user: dict = Depends(get_current_user)):
    try:
        logger.info("Fetching inspection stats...")
        
        # Get all inspections
        inspections_response = supabase.table("sheq_inspections").select("*").execute()
        inspections = rows(inspections_response)

        # Get all findings
        findings_response = supabase.table("sheq_findings").select("*").execute()
        findings = rows(findings_response)
        scope = viewer_scope(current_user)
        if scope is not None:
            inspections = [i for i in inspections if i.get("section") == scope]
            visible_ids = {i.get("id") for i in inspections}
            findings = [f for f in findings if f.get("inspection_id") in visible_ids]
        
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
        if scope is not None and scope not in stats["bySection"]:
            stats["bySection"][scope] = 0
        
        # Count by section
        for inspection in inspections:
            section = inspection.get("section")
            if section in stats["bySection"]:
                stats["bySection"][section] += 1

        # Count by inspector — each inspection can list several, comma-separated
        all_inspectors = (
            name.strip()
            for insp in inspections
            for name in insp.get("inspectors", "").split(",")
            if name.strip()
        )
        stats["byInspector"] = count_by(all_inspectors, lambda n: n)


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
        raise server_error(e, "Loading stats")

# GET single inspection
@router.get("/{inspection_id}", dependencies=[Depends(get_current_user)])
async def get_inspection(inspection_id: str, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Fetching inspection {inspection_id}")
        
        # Get inspection
        inspection_response = supabase.table("sheq_inspections")\
            .select("*")\
            .eq("id", inspection_id)\
            .execute()

        db_inspection = one_row(inspection_response)
        if db_inspection is None:
            raise HTTPException(status_code=404, detail="Inspection not found")
        assert_row_visible(current_user, "sheq_inspections", db_inspection, detail="Inspection not found")

        # Get findings
        findings_response = supabase.table("sheq_findings")\
            .select("*")\
            .eq("inspection_id", inspection_id)\
            .execute()

        db_findings = rows(findings_response)
        camel_findings = [map_db_finding_to_camel(f) for f in db_findings]

        camel_inspection = map_db_inspection_to_camel(db_inspection)
        camel_inspection["findings"] = camel_findings
        
        return camel_inspection
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching inspection: {str(e)}")
        raise server_error(e, "Loading inspection")

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
        
        scope = viewer_scope(current_user)
        inspection_data = stamp_create(current_user, "sheq_inspections", inspection_data)
        logger.info(f"Inserting inspection: {inspection_data}")
        
        inspection_response = supabase.table("sheq_inspections")\
            .insert(inspection_data)\
            .execute()

        created_inspection = one_row(inspection_response)
        if created_inspection is None:
            raise HTTPException(status_code=500, detail="Failed to create inspection")
        
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
                    "section": scope or finding.section,
                    "completeddate": finding.completedDate,    # lowercase
                    "remarks": finding.remarks
                })
            
            if findings_data:
                findings_response = supabase.table("sheq_findings")\
                    .insert(findings_data)\
                    .execute()

                db_findings = rows(findings_response)
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
        raise server_error(e, "Creating inspection")

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

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Inspection not found")

        # Prepare update data - map camelCase to lowercase for DB
        data_to_update = {}
        update_dict = updated.model_dump(exclude_unset=True)
        
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
            # `value is not None` used to be here too — the null-vs-unset bug already
            # fixed project-wide once (backend edec24a): exclude_unset=True already
            # excludes fields the caller never sent, so an explicit null reaching here
            # IS the caller asking to clear that field. Fixed 2026-08-30. The elif
            # below was already dead code before this fix (an empty list satisfied the
            # old `value is not None` check too, so the if branch always won for
            # before_photos/after_photos) and stays dead now — left as-is, harmless.
            if key in field_mapping and key != "findings":
                data_to_update[field_mapping[key]] = value
            # lists can be empty — treat separately so [] clears photos
            elif key in ("before_photos", "after_photos") and isinstance(value, list):
                data_to_update[key] = value
        
        scope = viewer_scope(current_user)
        if scope is not None:
            merged = {**existing_row, **data_to_update}
            assert_row_visible(current_user, "sheq_inspections", merged, detail="Inspection not found")
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        # Update inspection
        if data_to_update:
            update_response = supabase.table("sheq_inspections")\
                .update(data_to_update)\
                .eq("id", inspection_id)\
                .execute()

            updated_inspection = one_row(update_response)
            if updated_inspection is None:
                raise HTTPException(status_code=500, detail="Update failed")
        else:
            updated_inspection = existing_row
        
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
                        "section": scope or finding.section,
                        "completeddate": finding.completedDate,    # lowercase
                        "remarks": finding.remarks
                    })
                
                findings_response = supabase.table("sheq_findings")\
                    .insert(findings_data)\
                    .execute()

                db_findings = rows(findings_response)
                updated_inspection["findings"] = [map_db_finding_to_camel(f) for f in db_findings]
            else:
                updated_inspection["findings"] = []
        else:
            # Get existing findings
            findings_response = supabase.table("sheq_findings")\
                .select("*")\
                .eq("inspection_id", inspection_id)\
                .execute()

            db_findings = rows(findings_response)
            updated_inspection["findings"] = [map_db_finding_to_camel(f) for f in db_findings]
        
        # Map to camelCase for response
        result = map_db_inspection_to_camel(updated_inspection)
        result["findings"] = updated_inspection.get("findings", [])
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating inspection: {str(e)}")
        raise server_error(e, "Updating inspection")

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

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Inspection not found")
        assert_row_visible(current_user, "sheq_inspections", existing_row, detail="Inspection not found")

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
        raise server_error(e, "Deleting inspection")

# GET findings for an inspection
@router.get("/{inspection_id}/findings", dependencies=[Depends(get_current_user)])
async def get_findings(inspection_id: str, current_user: dict = Depends(get_current_user)):
    try:
        logger.info(f"Fetching findings for inspection {inspection_id}")
        if viewer_scope(current_user) is not None:
            parent = one_row(supabase.table("sheq_inspections").select("*").eq("id", inspection_id).execute())
            assert_row_visible(current_user, "sheq_inspections", parent, detail="Inspection not found")
        
        response = supabase.table("sheq_findings")\
            .select("*")\
            .eq("inspection_id", inspection_id)\
            .execute()
        
        db_findings = rows(response)
        return [map_db_finding_to_camel(f) for f in db_findings]
        
    except Exception as e:
        logger.error(f"Error fetching findings: {str(e)}")
        raise server_error(e, "Loading findings")

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

        parent_row = one_row(inspection)
        if parent_row is None:
            raise HTTPException(status_code=404, detail="Inspection not found")
        scope = viewer_scope(current_user)
        assert_row_visible(current_user, "sheq_inspections", parent_row, detail="Inspection not found")

        finding_data = {
            "id": generate_id(),
            "inspection_id": inspection_id,
            "finding": finding.finding,
            "requiredaction": finding.requiredAction,  # lowercase
            "bywho": finding.byWho,                    # lowercase
            "bywhen": finding.byWhen,                  # lowercase
            "status": finding.status,
            "priority": finding.priority,
            "section": scope or finding.section,
            "completeddate": finding.completedDate,    # lowercase
            "remarks": finding.remarks
        }
        
        response = supabase.table("sheq_findings")\
            .insert(finding_data)\
            .execute()

        db_finding = one_row(response)
        if db_finding is None:
            raise HTTPException(status_code=500, detail="Failed to add finding")
        return map_db_finding_to_camel(db_finding)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding finding: {str(e)}")
        raise server_error(e, "Adding finding")

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

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Finding not found")
        assert_row_visible(current_user, "sheq_findings", existing_row, supabase, detail="Finding not found")

        # Prepare update data
        data_to_update = {}
        update_dict = updated.model_dump(exclude_unset=True)

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
            # `value is not None` used to be here too — the null-vs-unset bug already
            # fixed project-wide once (backend edec24a): exclude_unset=True already
            # excludes fields the caller never sent, so an explicit null reaching here
            # IS the caller asking to clear that field. Fixed 2026-08-30.
            if key in field_mapping:
                data_to_update[field_mapping[key]] = value

        scope = viewer_scope(current_user)
        if scope is not None and data_to_update.get("section") is not None:
            # The finding's section is descriptive (visibility comes from
            # the parent): it may stay as it is or become the viewer's
            # scope — never anything else.
            new_section = data_to_update["section"]
            if new_section != scope and new_section != existing_row.get("section"):
                raise HTTPException(status_code=404, detail="Finding not found")
        if not data_to_update:
            return map_db_finding_to_camel(existing_row)

        response = supabase.table("sheq_findings")\
            .update(data_to_update)\
            .eq("id", finding_id)\
            .execute()

        db_finding = one_row(response)
        if db_finding is None:
            raise HTTPException(status_code=500, detail="Update failed")
        return map_db_finding_to_camel(db_finding)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating finding: {str(e)}")
        raise server_error(e, "Updating finding")

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

        existing_row = one_row(existing)
        if existing_row is None:
            raise HTTPException(status_code=404, detail="Finding not found")
        assert_row_visible(current_user, "sheq_findings", existing_row, supabase, detail="Finding not found")

        supabase.table("sheq_findings")\
            .delete()\
            .eq("id", finding_id)\
            .execute()
        
        return {"success": True, "message": "Finding deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting finding: {str(e)}")
        raise server_error(e, "Deleting finding")
