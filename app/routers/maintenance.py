# backend/app/routes/maintenance.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.cache import cached, cache_get, cache_set, build_key, invalidate_namespace
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()

# ==================== WORK ORDERS MODELS ====================
class JobType(BaseModel):
    operational: bool = False
    maintenance: bool = False
    mining: bool = False

class ManpowerRow(BaseModel):
    grade: Optional[str] = None
    required_number: Optional[str] = None  # Made optional
    required_unit_time: Optional[str] = None  # Made optional
    total_man_hours: Optional[str] = None  # Made optional

class WorkOrderCreate(BaseModel):
    # Header Information
    to_department: str
    to_section: str
    date_raised: date
    work_order_number: str
    from_department: str
    from_section: str
    time_raised: str
    account_number: str
    equipment_info: str
    user_lab_today: str
    
    # Job Type
    job_type: JobType
    job_request_details: str
    requested_by: str
    authorising_foreman: str
    authorising_engineer: str
    allocated_to: str
    estimated_hours: str
    responsible_foreman: str
    job_instructions: str
    
    # Manpower - Made optional with default
    manpower: Optional[List[ManpowerRow]] = None
    
    # Work Analysis
    work_done_details: str
    cause_of_failure: str
    delay_details: str
    
    # Sign-off
    artisan_name: str
    artisan_sign: str
    artisan_date: str
    foreman_name: str
    foreman_sign: str
    foreman_date: str
    
    # Time Tracking
    time_work_started: str
    time_work_finished: str
    total_time_worked: str
    overtime_start_time: str
    overtime_end_time: str
    overtime_hours: str
    delay_from_time: str
    delay_to_time: str
    total_delay_hours: str
    
    # Frontend compatibility fields
    title: Optional[str] = None
    description: Optional[str] = None
    status: str = "pending"
    priority: str = "medium"
    department: Optional[str] = None
    equipment: Optional[str] = None
    due_date: Optional[date] = None
    progress: int = 0
    notes: Optional[str] = None

    # Classification & analysis fields (previously stored only in browser localStorage —
    # now persisted server-side; requires the matching columns from migration
    # 2026-07_work_orders_classification.sql).
    classification: Optional[str] = None
    classification_custom: Optional[str] = None
    failure_mode: Optional[str] = None
    discipline: Optional[str] = None
    trade: Optional[str] = None
    spares_used: Optional[List[Dict[str, Any]]] = None

class WorkOrderUpdate(BaseModel):
    to_department: Optional[str] = None
    to_section: Optional[str] = None
    date_raised: Optional[date] = None
    work_order_number: Optional[str] = None
    from_department: Optional[str] = None
    from_section: Optional[str] = None
    time_raised: Optional[str] = None
    account_number: Optional[str] = None
    equipment_info: Optional[str] = None
    user_lab_today: Optional[str] = None
    job_type: Optional[JobType] = None
    job_request_details: Optional[str] = None
    requested_by: Optional[str] = None
    authorising_foreman: Optional[str] = None
    authorising_engineer: Optional[str] = None
    allocated_to: Optional[str] = None
    estimated_hours: Optional[str] = None
    responsible_foreman: Optional[str] = None
    job_instructions: Optional[str] = None
    manpower: Optional[List[ManpowerRow]] = None
    work_done_details: Optional[str] = None
    cause_of_failure: Optional[str] = None
    delay_details: Optional[str] = None
    artisan_name: Optional[str] = None
    artisan_sign: Optional[str] = None
    artisan_date: Optional[str] = None
    foreman_name: Optional[str] = None
    foreman_sign: Optional[str] = None
    foreman_date: Optional[str] = None
    time_work_started: Optional[str] = None
    time_work_finished: Optional[str] = None
    total_time_worked: Optional[str] = None
    overtime_start_time: Optional[str] = None
    overtime_end_time: Optional[str] = None
    overtime_hours: Optional[str] = None
    delay_from_time: Optional[str] = None
    delay_to_time: Optional[str] = None
    total_delay_hours: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    department: Optional[str] = None
    equipment: Optional[str] = None
    due_date: Optional[date] = None
    progress: Optional[int] = None
    notes: Optional[str] = None
    classification: Optional[str] = None
    classification_custom: Optional[str] = None
    failure_mode: Optional[str] = None
    discipline: Optional[str] = None
    trade: Optional[str] = None
    spares_used: Optional[List[Dict[str, Any]]] = None

# ==================== PPE MODELS (if not already separate) ====================
class PPEIssueCreate(BaseModel):
    employee_name: str = Field(..., min_length=1)
    employee_id: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    position: str = Field(..., min_length=1)
    ppe_type: str = Field(..., min_length=1)
    item_name: str = Field(..., min_length=1)
    size: Optional[str] = None
    issue_date: date
    expiry_date: Optional[date] = None
    condition: str = Field(default="good")
    status: str = Field(default="active")
    notes: Optional[str] = None
    issued_by: Optional[str] = None
    location: Optional[str] = None
    mine_section: Optional[str] = None

class PPEIssueUpdate(BaseModel):
    employee_name: Optional[str] = None
    employee_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    ppe_type: Optional[str] = None
    item_name: Optional[str] = None
    size: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    condition: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    issued_by: Optional[str] = None
    location: Optional[str] = None
    mine_section: Optional[str] = None

# ==================== UTILITY FUNCTIONS ====================
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

def convert_dates_to_iso(record):
    """Convert date objects to ISO format strings for JSON serialization"""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record

# Columns that are date/time types in Supabase — empty strings must become NULL
_DATE_FIELDS = {'date_raised', 'artisan_date', 'foreman_date', 'due_date'}
_TIME_FIELDS = {
    'time_raised', 'time_work_started', 'time_work_finished',
    'overtime_start_time', 'overtime_end_time',
    'delay_from_time', 'delay_to_time',
}

def prepare_data_for_db(data: dict) -> dict:
    """Prepare data for Supabase insert/update.

    - date/datetime objects → ISO strings
    - dicts/lists → kept as-is (Supabase handles JSONB natively; do NOT stringify)
    - empty strings in date or time columns → None (NULL), so PostgreSQL doesn't reject them
    """
    result = {}
    for key, value in data.items():
        if isinstance(value, (date, datetime)):
            result[key] = value.isoformat()
        elif isinstance(value, (dict, list)):
            # Pass Python objects directly — PostgREST serialises to JSONB automatically
            result[key] = value
        elif key in _DATE_FIELDS | _TIME_FIELDS and value == '':
            result[key] = None
        else:
            result[key] = value
    return result

def prepare_data_for_response(data: dict) -> dict:
    """Convert JSON strings back to objects for API response"""
    result = {}
    json_fields = ['job_type', 'manpower', 'spares_used']
    
    for key, value in data.items():
        if key in json_fields and value and isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                result[key] = value
        else:
            result[key] = value
    return result

# ==================== WORK ORDERS ENDPOINTS ====================
@router.get("/work-orders")
async def get_work_orders(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    department: Optional[str] = None,
    allocated_to: Optional[str] = None,
    to_department: Optional[str] = None
):
    cache_key = build_key(
        "work_orders", status=status, priority=priority, department=department,
        allocated_to=allocated_to, to_department=to_department,
    )
    cached_result = await cache_get(cache_key)
    if cached_result is not None:
        return cached_result
    try:
        query = supabase.table("work_orders").select("*")
        
        if status and status != 'all':
            query = query.eq("status", status)
        if priority and priority != 'all':
            query = query.eq("priority", priority)
        if department and department != 'all':
            query = query.eq("department", department)
        if allocated_to and allocated_to != 'all':
            query = query.eq("allocated_to", allocated_to)
        if to_department and to_department != 'all':
            query = query.eq("to_department", to_department)
            
        response = query.order("created_at", desc=True).execute()
        
        records = response.data or []
        processed_records = []
        for record in records:
            processed_record = prepare_data_for_response(record)
            processed_records.append(processed_record)

        await cache_set(cache_key, processed_records, ttl=60, namespace="work_orders")
        return processed_records

    except Exception as e:
        logger.error(f"Error fetching work orders: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work orders: {str(e)}")

@router.post("/work-orders")
async def create_work_order(work_order: WorkOrderCreate, current_user: dict = Depends(get_current_user)):
    try:
        data_to_insert = work_order.dict()
        
        # Set default title and description if not provided
        if not data_to_insert.get('title'):
            data_to_insert['title'] = data_to_insert['job_request_details'][:50] + '...' if len(data_to_insert['job_request_details']) > 50 else data_to_insert['job_request_details']
        
        if not data_to_insert.get('description'):
            data_to_insert['description'] = data_to_insert['job_request_details']
            
        if not data_to_insert.get('department'):
            data_to_insert['department'] = data_to_insert['to_department']
            
        if not data_to_insert.get('equipment'):
            data_to_insert['equipment'] = data_to_insert['equipment_info']
        
        # Handle optional manpower - ensure it's not None
        if data_to_insert.get('manpower') is None:
            data_to_insert['manpower'] = []
        
        # Prepare data for database
        data_to_insert = prepare_data_for_db(data_to_insert)
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        data_to_insert["updated_at"] = datetime.utcnow().isoformat()
        
        logger.info(f"Creating work order with data: {data_to_insert}")
        
        response = supabase.table("work_orders").insert(data_to_insert).execute()
        
        if response.data:
            result = prepare_data_for_response(response.data[0])
            await invalidate_namespace("work_orders")
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create work order")
            
    except Exception as e:
        logger.error(f"Error creating work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating work order: {str(e)}")

@router.get("/work-orders/{work_order_id}")
async def get_work_order(work_order_id: int):
    try:
        response = supabase.table("work_orders").select("*").eq("id", work_order_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Work order not found")
        
        result = prepare_data_for_response(response.data[0])
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order: {str(e)}")

@router.patch("/work-orders/{work_order_id}")
async def update_work_order(work_order_id: int, updated: WorkOrderUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing = supabase.table("work_orders").select("*").eq("id", work_order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Work order not found")
        
        data_to_update = {k: v for k, v in updated.dict().items() if v is not None}
        data_to_update = prepare_data_for_db(data_to_update)
        data_to_update["updated_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("work_orders").update(data_to_update).eq("id", work_order_id).execute()
        
        if response.data:
            result = prepare_data_for_response(response.data[0])
            await invalidate_namespace("work_orders")
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating work order: {str(e)}")

@router.delete("/work-orders/{work_order_id}")
async def delete_work_order(work_order_id: int, current_user: dict = Depends(require_role('manager'))):
    try:
        existing = supabase.table("work_orders").select("*").eq("id", work_order_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Work order not found")
        
        supabase.table("work_orders").delete().eq("id", work_order_id).execute()
        await invalidate_namespace("work_orders")
        return {"success": True, "message": "Work order deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting work order: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting work order: {str(e)}")

@router.get("/work-orders/allocated/{allocated_to}")
async def get_work_orders_by_allocated(allocated_to: str):
    try:
        response = supabase.table("work_orders").select("*").eq("allocated_to", allocated_to).order("created_at", desc=True).execute()
        
        records = response.data or []
        processed_records = []
        for record in records:
            processed_record = prepare_data_for_response(record)
            processed_records.append(processed_record)
            
        return processed_records
        
    except Exception as e:
        logger.error(f"Error fetching work orders by allocated: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work orders by allocated: {str(e)}")

# ==================== WORK ORDERS STATISTICS ====================
@router.get("/work-orders/stats/summary")
@cached("work_orders", ttl=60)
async def get_work_order_stats():
    try:
        # Get total records count
        records_response = supabase.table("work_orders").select("id", count="exact").execute()
        total_records = len(records_response.data) if records_response.data else 0
        
        # Get records by status
        status_response = supabase.table("work_orders").select("status").execute()
        status_counts = {}
        if status_response.data:
            for record in status_response.data:
                status = record.get('status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
        
        # Get records by priority
        priority_response = supabase.table("work_orders").select("priority").execute()
        priority_counts = {}
        if priority_response.data:
            for record in priority_response.data:
                priority = record.get('priority', 'unknown')
                priority_counts[priority] = priority_counts.get(priority, 0) + 1
        
        # Count overdue work orders
        today = date.today()
        records_all = supabase.table("work_orders").select("due_date, status").execute()
        overdue_count = 0
        
        if records_all.data:
            for record in records_all.data:
                due_date_str = record.get('due_date')
                status = record.get('status', 'pending')
                
                if due_date_str and status != 'completed':
                    try:
                        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                        if due_date < today:
                            overdue_count += 1
                    except (ValueError, TypeError):
                        continue
        
        # Calculate average progress
        progress_response = supabase.table("work_orders").select("progress").execute()
        total_progress = 0
        count_with_progress = 0
        
        if progress_response.data:
            for record in progress_response.data:
                progress = record.get('progress', 0)
                if progress is not None:
                    total_progress += progress
                    count_with_progress += 1
        
        avg_progress = round(total_progress / count_with_progress) if count_with_progress > 0 else 0
        
        return {
            "total_records": total_records,
            "status_breakdown": status_counts,
            "priority_breakdown": priority_counts,
            "overdue_count": overdue_count,
            "average_progress": avg_progress,
            "pending": status_counts.get('pending', 0),
            "in_progress": status_counts.get('in-progress', 0),
            "completed": status_counts.get('completed', 0),
            "on_hold": status_counts.get('on-hold', 0),
            "urgent": priority_counts.get('urgent', 0),
            "high": priority_counts.get('high', 0),
            "medium": priority_counts.get('medium', 0),
            "low": priority_counts.get('low', 0)
        }
        
    except Exception as e:
        logger.error(f"Error fetching work order stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching work order stats: {str(e)}")

# ==================== PPE ENDPOINTS (if you want them consolidated here) ====================
@router.get("/ppe")
async def get_ppe_records(
    status: Optional[str] = None,
    ppe_type: Optional[str] = None,
    department: Optional[str] = None,
    location: Optional[str] = None,
    employee_id: Optional[str] = None
):
    try:
        query = supabase.table("ppe_records").select("*")
        
        if status and status != 'all':
            query = query.eq("status", status)
        if ppe_type and ppe_type != 'all':
            query = query.eq("ppe_type", ppe_type)
        if department and department != 'all':
            query = query.eq("department", department)
        if location and location != 'all':
            query = query.eq("location", location)
        if employee_id and employee_id != 'all':
            query = query.eq("employee_id", employee_id)
            
        response = query.order("created_at", desc=True).execute()
        
        records = response.data or []
        for record in records:
            convert_dates_to_iso(record)
            
        return records
        
    except Exception as e:
        logger.error(f"Error fetching PPE records: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching PPE records: {str(e)}")

@router.post("/ppe")
async def create_ppe_record(record: PPEIssueCreate, current_user: dict = Depends(get_current_user)):
    try:
        data_to_insert = record.dict()
        
        if data_to_insert.get('issue_date'):
            data_to_insert['issue_date'] = data_to_insert['issue_date'].isoformat()
        if data_to_insert.get('expiry_date'):
            data_to_insert['expiry_date'] = data_to_insert['expiry_date'].isoformat()
            
        data_to_insert["created_at"] = datetime.utcnow().isoformat()
        
        response = supabase.table("ppe_records").insert(data_to_insert).execute()
        
        if response.data:
            result = response.data[0]
            convert_dates_to_iso(result)
            return result
        else:
            raise HTTPException(status_code=500, detail="Failed to create PPE record")
            
    except Exception as e:
        logger.error(f"Error creating PPE record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating PPE record: {str(e)}")

# ==================== MAINTENANCE DASHBOARD STATS ====================
@router.get("/dashboard/stats")
async def get_maintenance_dashboard_stats():
    """Combined stats for maintenance dashboard"""
    try:
        # Get work order stats
        work_order_stats = await get_work_order_stats()
        
        # Get PPE stats (you can add PPE stats here too)
        ppe_response = supabase.table("ppe_records").select("id", count="exact").execute()
        total_ppe = len(ppe_response.data) if ppe_response.data else 0
        
        # Calculate overall efficiency
        total_work_orders = work_order_stats["total_records"]
        completed_work_orders = work_order_stats["completed"]
        efficiency = round((completed_work_orders / total_work_orders * 100)) if total_work_orders > 0 else 0
        
        return {
            "work_orders": work_order_stats,
            "ppe_count": total_ppe,
            "overall_efficiency": efficiency,
            "total_maintenance_items": total_work_orders + total_ppe
        }
        
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching dashboard stats: {str(e)}")

# Health check endpoint
@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "maintenance"}