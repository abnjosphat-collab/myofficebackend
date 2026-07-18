from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, date, timedelta
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
import logging
import json
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom JSON encoder to handle date objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# ========== EMPLOYEE MODELS ==========
class EmployeeBase(BaseModel):
    employee_id: str = Field(..., min_length=1, description="Unique employee identifier")
    name: str = Field(..., min_length=1, description="Full name of employee")
    department: str = Field(..., min_length=1, description="Department name")
    position: Optional[str] = Field(None, description="Job position/title")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    employment_date: Optional[date] = Field(None, description="Date of employment")
    status: str = Field(default="active", description="Employment status")
    supervisor_id: Optional[str] = Field(None, description="Supervisor's employee ID")
    work_location: Optional[str] = Field(None, description="Primary work location")
    shift: Optional[str] = Field(None, description="Work shift")
    mine_section: Optional[str] = Field(None, description="Mine section/area")

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class EmployeeCreate(EmployeeBase):
    pass

class EmployeeUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    employment_date: Optional[date] = None
    status: Optional[str] = None
    supervisor_id: Optional[str] = None
    work_location: Optional[str] = None
    shift: Optional[str] = None
    mine_section: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class EmployeeResponse(EmployeeBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# ========== SHEQ REPORT MODELS ==========
class SHEQReportBase(BaseModel):
    report_type: str = Field(..., min_length=1, description="Type of report: hazard, near_miss, incident, pto")
    employee_name: str = Field(..., min_length=1, description="Name of employee involved")
    employee_id: str = Field(..., min_length=1, description="Employee ID")
    department: str = Field(..., min_length=1, description="Employee department")
    position: Optional[str] = Field(None, description="Employee position")
    location: str = Field(..., min_length=1, description="Location where incident occurred")
    date_reported: date = Field(..., description="Date when report was filed")
    time_reported: Optional[str] = Field(None, description="Time when report was filed (HH:MM)")
    priority: str = Field(default="medium", description="Priority level: low, medium, high, critical")
    status: str = Field(default="open", description="Status: draft, open, in_progress, under_review, resolved, closed")
    
    # Hazard Report Fields
    hazard_description: Optional[str] = None
    risk_assessment: Optional[str] = None
    suggested_improvements: Optional[str] = None
    requirements: Optional[str] = None
    
    # Near Miss Fields
    near_miss_description: Optional[str] = None
    potential_severity: Optional[str] = None
    immediate_causes: Optional[str] = None
    root_causes: Optional[str] = None
    
    # Incident Fields
    incident_description: Optional[str] = None
    incident_type: Optional[str] = None
    injury_type: Optional[str] = None
    property_damage: Optional[str] = None
    environmental_impact: Optional[str] = None
    immediate_actions: Optional[str] = None
    
    # PTO Fields
    supervisor_name: Optional[str] = None
    supervisor_id: Optional[str] = None
    employee_observed: Optional[str] = None
    employee_observed_id: Optional[str] = None
    task_observed: Optional[str] = None
    safe_behaviors: Optional[str] = None
    at_risk_behaviors: Optional[str] = None
    recommendations: Optional[str] = None
    
    # Common Fields
    description: Optional[str] = None
    corrective_actions: Optional[str] = None
    responsible_person: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[date] = None
    completion_date: Optional[date] = None
    notes: Optional[str] = None
    reported_by: Optional[str] = None
    mine_section: Optional[str] = None
    #attachments: Optional[List[str]] = None

    @validator('priority')
    def validate_priority(cls, v):
        valid_priorities = ['low', 'medium', 'high', 'critical']
        if v not in valid_priorities:
            raise ValueError(f'Priority must be one of: {", ".join(valid_priorities)}')
        return v

    @validator('status')
    def validate_status(cls, v):
        valid_statuses = ['draft', 'open', 'in_progress', 'under_review', 'resolved', 'closed']
        if v not in valid_statuses:
            raise ValueError(f'Status must be one of: {", ".join(valid_statuses)}')
        return v

    @validator('report_type')
    def validate_report_type(cls, v):
        valid_types = ['hazard', 'near_miss', 'incident', 'pto']
        if v not in valid_types:
            raise ValueError(f'Report type must be one of: {", ".join(valid_types)}')
        return v

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class SHEQReportCreate(SHEQReportBase):
    pass

class SHEQReportUpdate(BaseModel):
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    location: Optional[str] = None
    date_reported: Optional[date] = None
    time_reported: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    
    # Hazard Report Fields
    hazard_description: Optional[str] = None
    risk_assessment: Optional[str] = None
    suggested_improvements: Optional[str] = None
    requirements: Optional[str] = None
    
    # Near Miss Fields
    near_miss_description: Optional[str] = None
    potential_severity: Optional[str] = None
    immediate_causes: Optional[str] = None
    root_causes: Optional[str] = None
    
    # Incident Fields
    incident_description: Optional[str] = None
    incident_type: Optional[str] = None
    injury_type: Optional[str] = None
    property_damage: Optional[str] = None
    environmental_impact: Optional[str] = None
    immediate_actions: Optional[str] = None
    
    # PTO Fields
    supervisor_name: Optional[str] = None
    supervisor_id: Optional[str] = None
    employee_observed: Optional[str] = None
    employee_observed_id: Optional[str] = None
    task_observed: Optional[str] = None
    safe_behaviors: Optional[str] = None
    at_risk_behaviors: Optional[str] = None
    recommendations: Optional[str] = None
    
    # Common Fields
    description: Optional[str] = None
    corrective_actions: Optional[str] = None
    responsible_person: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[date] = None
    completion_date: Optional[date] = None
    notes: Optional[str] = None
    reported_by: Optional[str] = None
    mine_section: Optional[str] = None
    attachments: Optional[List[str]] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class SHEQReportResponse(SHEQReportBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True

# ========== HELPER FUNCTIONS ==========
def convert_dates_to_iso(record):
    """Convert date objects to ISO format strings for JSON serialization"""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
            elif isinstance(value, list):
                # Handle lists that might contain date objects
                record[key] = [convert_dates_to_iso(item) if isinstance(item, dict) else item for item in value]
    return record

def format_supabase_response(response):
    """Format Supabase response and convert dates"""
    if not response.data:
        return []
    
    records = response.data
    for record in records:
        convert_dates_to_iso(record)
    return records

# ========== EMPLOYEE ENDPOINTS ==========
@router.get("/employees", response_model=List[EmployeeResponse])
async def get_employees(
    department: Optional[str] = Query(None, description="Filter by department"),
    status: Optional[str] = Query(None, description="Filter by status"),
    location: Optional[str] = Query(None, description="Filter by work location"),
    search: Optional[str] = Query(None, description="Search by name, ID, or email"),
    limit: int = Query(100, ge=1, le=1000, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
):
    """
    Get all employees with optional filtering
    """
    try:
        query = supabase.table("employees").select("*")
        
        # Apply filters
        if department and department != 'all':
            query = query.eq("department", department)
        if status and status != 'all':
            query = query.eq("status", status)
        if location:
            query = query.eq("work_location", location)
        if search:
            query = query.or_(f"name.ilike.%{search}%,employee_id.ilike.%{search}%,email.ilike.%{search}%,department.ilike.%{search}%")
        
        # Execute query with pagination
        response = query.order("name").range(offset, offset + limit - 1).execute()
        
        return format_supabase_response(response)
        
    except Exception as e:
        logger.error(f"Error fetching employees: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching employees: {str(e)}")

@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
async def get_employee(employee_id: str):
    """
    Get a specific employee by ID
    """
    try:
        response = supabase.table("employees").select("*").eq("employee_id", employee_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        record = response.data[0]
        convert_dates_to_iso(record)
        return record
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching employee: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching employee: {str(e)}")

@router.post("/employees", response_model=EmployeeResponse)
async def create_employee(employee: EmployeeCreate, current_user: dict = Depends(get_current_user)):
    """
    Create a new employee
    """
    try:
        # Check if employee already exists
        existing = supabase.table("employees").select("*").eq("employee_id", employee.employee_id).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Employee ID already exists")
        
        # Convert Pydantic model to dict and handle date serialization
        data_to_insert = employee.dict()
        
        # Ensure dates are properly formatted for database
        if data_to_insert.get("employment_date"):
            data_to_insert["employment_date"] = data_to_insert["employment_date"].isoformat()
        
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("employees").insert(data_to_insert).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create employee")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating employee: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating employee: {str(e)}")

@router.patch("/employees/{employee_id}", response_model=EmployeeResponse)
async def update_employee(employee_id: str, updated: EmployeeUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update an existing employee
    """
    try:
        # Check if employee exists
        existing = supabase.table("employees").select("*").eq("employee_id", employee_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Convert Pydantic model to dict and handle date serialization
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        
        # Ensure dates are properly formatted for database
        if data_to_update.get("employment_date"):
            data_to_update["employment_date"] = data_to_update["employment_date"].isoformat()
        
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("employees").update(data_to_update).eq("employee_id", employee_id).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to update employee")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating employee: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating employee: {str(e)}")

@router.delete("/employees/{employee_id}")
async def delete_employee(employee_id: str, current_user: dict = Depends(require_role('manager'))):
    """
    Delete an employee (soft delete by setting status to inactive)
    """
    try:
        # Check if employee exists
        existing = supabase.table("employees").select("*").eq("employee_id", employee_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Employee not found")
        
        # Soft delete by setting status to inactive
        data_to_update = {
            "status": "inactive",
            "updated_at": datetime.utcnow().isoformat()
        }
        
        response = supabase.table("employees").update(data_to_update).eq("employee_id", employee_id).execute()
        
        if response.data:
            return {"success": True, "message": "Employee deactivated successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to deactivate employee")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting employee: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error deleting employee: {str(e)}")

@router.get("/employees/departments/list")
async def get_departments():
    """
    Get unique departments from employees table
    """
    try:
        response = supabase.table("employees").select("department").execute()
        
        if not response.data:
            return []
        
        # Extract unique departments
        departments = sorted(set(
            record["department"] for record in response.data 
            if record.get("department") and record.get("status") == "active"
        ))
        return departments
        
    except Exception as e:
        logger.error(f"Error fetching departments: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching departments: {str(e)}")

@router.get("/employees/search/quick")
async def search_employees(
    q: Optional[str] = Query(None, description="Search query"),
    department: Optional[str] = Query(None, description="Filter by department"),
    location: Optional[str] = Query(None, description="Filter by work location"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results")
):
    """
    Search employees by name, ID, or department
    """
    try:
        query = supabase.table("employees").select("*").eq("status", "active")
        
        if q:
            query = query.or_(f"name.ilike.%{q}%,employee_id.ilike.%{q}%,email.ilike.%{q}%,department.ilike.%{q}%")
        
        if department:
            query = query.eq("department", department)
        
        if location:
            query = query.eq("work_location", location)
        
        response = query.order("name").limit(limit).execute()
        
        records = response.data or []
        for record in records:
            convert_dates_to_iso(record)
            
        return records
        
    except Exception as e:
        logger.error(f"Error searching employees: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error searching employees: {str(e)}")

# ========== SHEQ REPORTS ENDPOINTS ==========
@router.get("", response_model=List[SHEQReportResponse])
async def get_sheq_reports(
    report_type: Optional[str] = Query(None, description="Filter by report type"),
    status: Optional[str] = Query(None, description="Filter by status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    department: Optional[str] = Query(None, description="Filter by department"),
    location: Optional[str] = Query(None, description="Filter by location"),
    employee_id: Optional[str] = Query(None, description="Filter by employee ID"),
    date_from: Optional[date] = Query(None, description="Filter from date"),
    date_to: Optional[date] = Query(None, description="Filter to date"),
    search: Optional[str] = Query(None, description="Search in reports"),
    limit: int = Query(100, ge=1, le=1000, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
):
    """
    Get all SHEQ reports with optional filtering
    """
    try:
        query = supabase.table("sheq_reports").select("*")
        
        # Apply filters
        if report_type and report_type != 'all':
            query = query.eq("report_type", report_type)
        if status and status != 'all':
            query = query.eq("status", status)
        if priority and priority != 'all':
            query = query.eq("priority", priority)
        if department and department != 'all':
            query = query.eq("department", department)
        if location and location != 'all':
            query = query.eq("location", location)
        if employee_id and employee_id != 'all':
            query = query.eq("employee_id", employee_id)
        if date_from:
            query = query.gte("date_reported", date_from.isoformat())
        if date_to:
            query = query.lte("date_reported", date_to.isoformat())
        if search:
            query = query.or_(
                f"employee_name.ilike.%{search}%,"
                f"employee_id.ilike.%{search}%,"
                f"location.ilike.%{search}%,"
                f"description.ilike.%{search}%,"
                f"hazard_description.ilike.%{search}%,"
                f"incident_description.ilike.%{search}%,"
                f"near_miss_description.ilike.%{search}%"
            )
        
        # Execute query with pagination and ordering
        response = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        return format_supabase_response(response)
        
    except Exception as e:
        logger.error(f"Error fetching SHEQ reports: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching SHEQ reports: {str(e)}")

@router.post("", response_model=SHEQReportResponse)
async def create_sheq_report(report: SHEQReportCreate, current_user: dict = Depends(get_current_user)):
    """
    Create a new SHEQ report
    """
    try:
        # Convert Pydantic model to dict and handle date serialization
        data_to_insert = report.dict()
        
        # Ensure dates are properly formatted for database
        date_fields = ['date_reported', 'due_date', 'completion_date']
        for field in date_fields:
            if data_to_insert.get(field):
                data_to_insert[field] = data_to_insert[field].isoformat()
            
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        # Set reported_by if not provided
        if not data_to_insert.get("reported_by"):
            data_to_insert["reported_by"] = data_to_insert.get("employee_name")
        
        response = supabase.table("sheq_reports").insert(data_to_insert).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create SHEQ report")
            
    except Exception as e:
        logger.error(f"Error creating SHEQ report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error creating SHEQ report: {str(e)}")

@router.get("/{report_id}", response_model=SHEQReportResponse)
async def get_sheq_report(report_id: int):
    """
    Get a specific SHEQ report by ID
    """
    try:
        response = supabase.table("sheq_reports").select("*").eq("id", report_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="SHEQ report not found")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching SHEQ report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching SHEQ report: {str(e)}")

@router.patch("/{report_id}", response_model=SHEQReportResponse)
async def update_sheq_report(report_id: int, updated: SHEQReportUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update an existing SHEQ report
    """
    try:
        existing = supabase.table("sheq_reports").select("*").eq("id", report_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="SHEQ report not found")
        
        # exclude_unset, not a None-filter: an explicitly-sent null must clear the
        # field, not be silently dropped. See work_orders (backend edec24a).
        data_to_update = updated.model_dump(exclude_unset=True)
        
        # Convert dates to ISO format for database
        date_fields = ['date_reported', 'due_date', 'completion_date']
        for field in date_fields:
            if data_to_update.get(field):
                data_to_update[field] = data_to_update[field].isoformat()
            
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("sheq_reports").update(data_to_update).eq("id", report_id).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating SHEQ report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating SHEQ report: {str(e)}")

@router.delete("/{report_id}")
async def delete_sheq_report(report_id: int, current_user: dict = Depends(require_role('manager'))):
    """
    Delete a SHEQ report
    """
    try:
        existing = supabase.table("sheq_reports").select("*").eq("id", report_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="SHEQ report not found")
        
        supabase.table("sheq_reports").delete().eq("id", report_id).execute()
        return {"success": True, "message": "SHEQ report deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting SHEQ report: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error deleting SHEQ report: {str(e)}")

@router.get("/employee/{employee_id}", response_model=List[SHEQReportResponse])
async def get_employee_sheq_reports(
    employee_id: str,
    limit: int = Query(50, ge=1, le=200, description="Number of records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
):
    """
    Get all SHEQ reports for a specific employee
    """
    try:
        response = supabase.table("sheq_reports")\
            .select("*")\
            .eq("employee_id", employee_id)\
            .order("date_reported", desc=True)\
            .range(offset, offset + limit - 1)\
            .execute()
        
        return format_supabase_response(response)
        
    except Exception as e:
        logger.error(f"Error fetching employee SHEQ reports: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching employee SHEQ reports: {str(e)}")

# ========== STATISTICS ENDPOINTS ==========
@router.get("/stats/summary")
async def get_sheq_stats(
    date_from: Optional[date] = Query(None, description="Start date for statistics"),
    date_to: Optional[date] = Query(None, description="End date for statistics")
):
    """
    Get SHEQ statistics summary
    """
    try:
        # Build base query
        query = supabase.table("sheq_reports").select("*")
        
        # Apply date filters if provided
        if date_from:
            query = query.gte("date_reported", date_from.isoformat())
        if date_to:
            query = query.lte("date_reported", date_to.isoformat())
        
        response = query.execute()
        all_reports = response.data or []
        
        if not all_reports:
            return {
                "total_reports": 0,
                "open_reports": 0,
                "in_progress_reports": 0,
                "resolved_reports": 0,
                "overdue_actions": 0,
                "reports_by_type": {},
                "reports_by_status": {},
                "reports_by_priority": {},
                "reports_by_department": {},
                "reports_by_location": {},
                "trend_last_7_days": {}
            }
        
        # Calculate statistics
        today = date.today()
        
        total_reports = len(all_reports)
        open_reports = len([r for r in all_reports if r.get('status') == 'open'])
        in_progress_reports = len([r for r in all_reports if r.get('status') == 'in_progress'])
        resolved_reports = len([r for r in all_reports if r.get('status') in ['resolved', 'closed']])
        
        # Count overdue actions
        overdue = 0
        for report in all_reports:
            due_date_str = report.get('due_date')
            status = report.get('status', 'open')
            
            if due_date_str and status in ['open', 'in_progress', 'under_review']:
                try:
                    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                    if due_date < today:
                        overdue += 1
                except (ValueError, TypeError):
                    continue
        
        # Reports by type
        reports_by_type = {}
        for report in all_reports:
            report_type = report.get('report_type', 'unknown')
            reports_by_type[report_type] = reports_by_type.get(report_type, 0) + 1
        
        # Reports by status
        reports_by_status = {}
        for report in all_reports:
            status = report.get('status', 'unknown')
            reports_by_status[status] = reports_by_status.get(status, 0) + 1
        
        # Reports by priority
        reports_by_priority = {}
        for report in all_reports:
            priority = report.get('priority', 'unknown')
            reports_by_priority[priority] = reports_by_priority.get(priority, 0) + 1
        
        # Reports by department
        reports_by_department = {}
        for report in all_reports:
            department = report.get('department', 'unknown')
            reports_by_department[department] = reports_by_department.get(department, 0) + 1
        
        # Reports by location
        reports_by_location = {}
        for report in all_reports:
            location = report.get('location', 'unknown')
            reports_by_location[location] = reports_by_location.get(location, 0) + 1
        
        # Trend data for last 7 days
        trend_last_7_days = {}
        for i in range(6, -1, -1):
            date_key = (today - timedelta(days=i)).isoformat()
            trend_last_7_days[date_key] = 0
        
        for report in all_reports:
            date_reported = report.get('date_reported')
            if date_reported:
                try:
                    if isinstance(date_reported, str):
                        report_date = datetime.strptime(date_reported, '%Y-%m-%d').date()
                    else:
                        report_date = date_reported
                    
                    date_str = report_date.isoformat()
                    if date_str in trend_last_7_days:
                        trend_last_7_days[date_str] += 1
                except (ValueError, TypeError):
                    continue
        
        return {
            "total_reports": total_reports,
            "open_reports": open_reports,
            "in_progress_reports": in_progress_reports,
            "resolved_reports": resolved_reports,
            "overdue_actions": overdue,
            "reports_by_type": reports_by_type,
            "reports_by_status": reports_by_status,
            "reports_by_priority": reports_by_priority,
            "reports_by_department": reports_by_department,
            "reports_by_location": reports_by_location,
            "trend_last_7_days": trend_last_7_days
        }
        
    except Exception as e:
        logger.error(f"Error fetching SHEQ stats: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching SHEQ stats: {str(e)}")

@router.get("/stats/employee/{employee_id}")
async def get_employee_stats(employee_id: str):
    """
    Get statistics for a specific employee
    """
    try:
        response = supabase.table("sheq_reports").select("*").eq("employee_id", employee_id).execute()
        reports = response.data or []
        
        if not reports:
            return {
                "total_reports": 0,
                "reports_by_type": {},
                "reports_by_status": {},
                "first_report_date": None,
                "last_report_date": None
            }
        
        total_reports = len(reports)
        
        # Reports by type
        reports_by_type = {}
        for report in reports:
            report_type = report.get('report_type', 'unknown')
            reports_by_type[report_type] = reports_by_type.get(report_type, 0) + 1
        
        # Reports by status
        reports_by_status = {}
        for report in reports:
            status = report.get('status', 'unknown')
            reports_by_status[status] = reports_by_status.get(status, 0) + 1
        
        # Dates
        dates = []
        for report in reports:
            date_reported = report.get('date_reported')
            if date_reported:
                try:
                    if isinstance(date_reported, str):
                        report_date = datetime.strptime(date_reported, '%Y-%m-%d').date()
                    else:
                        report_date = date_reported
                    dates.append(report_date)
                except (ValueError, TypeError):
                    continue
        
        first_report_date = min(dates).isoformat() if dates else None
        last_report_date = max(dates).isoformat() if dates else None
        
        return {
            "total_reports": total_reports,
            "reports_by_type": reports_by_type,
            "reports_by_status": reports_by_status,
            "first_report_date": first_report_date,
            "last_report_date": last_report_date
        }
        
    except Exception as e:
        logger.error(f"Error fetching employee stats: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching employee stats: {str(e)}")

# ========== COMBINED ENDPOINTS ==========
@router.get("/combined/employees")
async def get_combined_employees(
    search: Optional[str] = Query(None, description="Search query"),
    department: Optional[str] = Query(None, description="Filter by department"),
    limit: int = Query(100, ge=1, le=500, description="Maximum results")
):
    """
    Get combined employee list from both employees table and SHEQ reports
    """
    try:
        employees_map = {}
        
        # 1. Get from employees table
        try:
            employees_query = supabase.table("employees").select("employee_id, name, department, position, email, status")
            if department and department != 'all':
                employees_query = employees_query.eq("department", department)
            
            employees_response = employees_query.execute()
            
            if employees_response.data:
                for record in employees_response.data:
                    employee_id = record.get('employee_id')
                    if employee_id and record.get('status') == 'active':
                        employees_map[employee_id] = {
                            'employee_id': employee_id,
                            'name': record.get('name'),
                            'department': record.get('department'),
                            'position': record.get('position'),
                            'email': record.get('email'),
                            'source': 'employees_table'
                        }
        except Exception as e:
            logger.warning(f"Could not fetch from employees table: {str(e)}")
        
        # 2. Get from SHEQ reports (for employees not in employees table)
        try:
            sheq_query = supabase.table("sheq_reports").select("employee_id, employee_name, department, position").distinct("employee_id")
            if department and department != 'all':
                sheq_query = sheq_query.eq("department", department)
            
            sheq_response = sheq_query.execute()
            
            if sheq_response.data:
                for record in sheq_response.data:
                    employee_id = record.get('employee_id')
                    if employee_id and employee_id not in employees_map:
                        employees_map[employee_id] = {
                            'employee_id': employee_id,
                            'name': record.get('employee_name'),
                            'department': record.get('department'),
                            'position': record.get('position'),
                            'email': None,
                            'source': 'sheq_reports'
                        }
        except Exception as e:
            logger.warning(f"Could not fetch from SHEQ reports: {str(e)}")
        
        # Convert to list
        employees_list = list(employees_map.values())
        
        # Apply search filter if provided
        if search:
            search_lower = search.lower()
            employees_list = [
                emp for emp in employees_list
                if (search_lower in emp.get('name', '').lower() or
                    search_lower in emp.get('employee_id', '').lower() or
                    search_lower in emp.get('department', '').lower())
            ]
        
        # Sort by name
        employees_list.sort(key=lambda x: x.get('name', '').lower())
        
        # Apply limit
        employees_list = employees_list[:limit]
        
        return employees_list
        
    except Exception as e:
        logger.error(f"Error fetching combined employees: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching combined employees: {str(e)}")

@router.get("/dropdowns/locations")
async def get_location_dropdown():
    """
    Get unique locations for dropdown
    """
    try:
        response = supabase.table("sheq_reports").select("location").execute()
        
        if not response.data:
            return []
        
        locations = sorted(set(
            record["location"] for record in response.data 
            if record.get("location")
        ))
        return locations
        
    except Exception as e:
        logger.error(f"Error fetching locations: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching locations: {str(e)}")

@router.get("/dropdowns/departments")
async def get_department_dropdown():
    """
    Get unique departments for dropdown from both sources
    """
    try:
        departments_set = set()
        
        # From employees table
        try:
            emp_response = supabase.table("employees")\
                .select("department")\
                .eq("status", "active")\
                .execute()
            
            if emp_response.data:
                for record in emp_response.data:
                    if record.get("department"):
                        departments_set.add(record["department"])
        except Exception as e:
            logger.warning(f"Could not fetch departments from employees table: {str(e)}")
        
        # From SHEQ reports
        try:
            sheq_response = supabase.table("sheq_reports").select("department").execute()
            if sheq_response.data:
                for record in sheq_response.data:
                    if record.get("department"):
                        departments_set.add(record["department"])
        except Exception as e:
            logger.warning(f"Could not fetch departments from SHEQ reports: {str(e)}")
        
        return sorted(departments_set)
        
    except Exception as e:
        logger.error(f"Error fetching departments dropdown: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error fetching departments dropdown: {str(e)}")

# ========== HEALTH CHECK ==========
@router.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    try:
        # Test database connection
        supabase.table("sheq_reports").select("id").limit(1).execute()
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {str(e)}")

# ========== BULK OPERATIONS ==========
@router.post("/bulk/status-update")
async def bulk_update_status(
    report_ids: List[int],
    status: str,
    notes: Optional[str] = None
, current_user: dict = Depends(get_current_user)):
    """
    Bulk update status of multiple reports
    """
    try:
        if not report_ids:
            raise HTTPException(status_code=400, detail="No report IDs provided")
        
        # Validate status
        valid_statuses = ['open', 'in_progress', 'under_review', 'resolved', 'closed']
        if status not in valid_statuses:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
            )
        
        data_to_update = {
            "status": status,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        if notes:
            data_to_update["notes"] = notes
        
        # Update each report
        updated_reports = []
        for report_id in report_ids:
            response = supabase.table("sheq_reports")\
                .update(data_to_update)\
                .eq("id", report_id)\
                .execute()
            
            if response.data:
                updated_reports.append(response.data[0])
        
        return {
            "success": True,
            "message": f"Updated {len(updated_reports)} reports to status '{status}'",
            "updated_count": len(updated_reports)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk status update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in bulk status update: {str(e)}")