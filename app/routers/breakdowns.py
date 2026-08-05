# app/routers/breakdowns.py
from fastapi import APIRouter, HTTPException, Query, Depends
from app.auth import get_current_user, require_role
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, timedelta
import json
import logging
import os
from supabase import create_client, Client
from collections import defaultdict
from app.cache import cached, cache_get, cache_set, build_key, invalidate_namespace

# Configure logging
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/api/breakdowns", tags=["breakdowns"])

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# Supabase configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Initialize Supabase client
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized")
    except Exception as e:
        logger.error(f"❌ Error initializing Supabase: {e}")
        supabase = None
else:
    logger.warning("⚠️ Supabase credentials not set")
    supabase = None

# Pydantic Models
class SparePart(BaseModel):
    name: str = Field(..., min_length=1)
    quantity: int = Field(1, ge=1)
    part_number: Optional[str] = None
    unit_price: float = Field(0.0, ge=0.0)
    total_cost: float = Field(0.0, ge=0.0)

class BreakdownCreate(BaseModel):
    # Basic info - MATCHES DATABASE COLUMNS
    machine_id: str = Field(..., min_length=1)
    machine_name: str = Field(..., min_length=1)
    artisan_name: str = Field(..., min_length=1)
    department: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)
    breakdown_date: str  # Changed from 'date' to 'breakdown_date'
    breakdown_type: str = Field(..., min_length=1)
    
    # Description fields - database has both
    breakdown_description: Optional[str] = None
    machine_description: Optional[str] = None
    
    # Work details
    work_done: Optional[str] = None
    artisan_recommendations: Optional[str] = None
    
    # Time tracking - strings for HH:MM format
    breakdown_start: Optional[str] = None
    breakdown_end: Optional[str] = None
    work_start: Optional[str] = None
    work_end: Optional[str] = None
    
    # Spare parts
    spares_used: List[SparePart] = []
    
    # Status
    status: str = Field(default="logged")
    priority: str = Field(default="medium")

    # Validator to handle description fields
    @validator('breakdown_date', pre=True)
    def validate_date(cls, v):
        if not v:
            return datetime.utcnow().strftime('%Y-%m-%d')
        return v

    @validator('machine_description', pre=True, always=True)
    def populate_machine_description(cls, v, values):
        # If machine_description not provided but breakdown_description is, use it
        if v is None and 'breakdown_description' in values:
            return values.get('breakdown_description') or ''
        return v or ''

class BreakdownUpdate(BaseModel):
    # All fields optional for updates
    machine_id: Optional[str] = None
    machine_name: Optional[str] = None
    artisan_name: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    breakdown_date: Optional[str] = None
    breakdown_type: Optional[str] = None
    breakdown_description: Optional[str] = None
    machine_description: Optional[str] = None
    work_done: Optional[str] = None
    artisan_recommendations: Optional[str] = None
    breakdown_start: Optional[str] = None
    breakdown_end: Optional[str] = None
    work_start: Optional[str] = None
    work_end: Optional[str] = None
    spares_used: Optional[List[SparePart]] = None
    status: Optional[str] = None
    priority: Optional[str] = None

# Helper Functions
def time_to_minutes(time_str: str) -> int:
    """Convert HH:MM time string to minutes"""
    if not time_str:
        return 0
    try:
        # Handle both HH:MM and HH:MM:SS formats
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes
    except:
        return 0

def calculate_time_metrics(data: dict) -> dict:
    """Calculate all time-based metrics - ONLY COLUMNS THAT EXIST IN DATABASE"""
    try:
        b_start = time_to_minutes(data.get('breakdown_start'))
        b_end = time_to_minutes(data.get('breakdown_end'))
        w_start = time_to_minutes(data.get('work_start'))
        w_end = time_to_minutes(data.get('work_end'))
        
        response_time = max(0, w_start - b_start) if b_start and w_start else 0
        repair_time = max(0, w_end - w_start) if w_start and w_end else 0
        
        if b_start and b_end:
            downtime = max(0, b_end - b_start)
        else:
            downtime = 0
        
        net_downtime = downtime  # Could subtract delays if you have delay fields
        
        # RETURN ONLY COLUMNS THAT EXIST IN YOUR DATABASE SCHEMA
        return {
            "response_time_minutes": response_time,
            "repair_time_minutes": repair_time,
            "downtime_minutes": downtime,
            "net_downtime_minutes": net_downtime
            # Note: Your database doesn't have *_hours columns
        }
    except Exception as e:
        logger.error(f"Error calculating time metrics: {e}")
        return {
            "response_time_minutes": 0,
            "repair_time_minutes": 0,
            "downtime_minutes": 0,
            "net_downtime_minutes": 0
        }

def calculate_spare_costs(spares: List[SparePart]) -> dict:
    """Calculate spare part costs"""
    try:
        total_cost = 0.0
        spares_with_costs = []
        
        for spare in spares:
            spare_dict = spare.dict()
            # Ensure total_cost is calculated
            quantity = spare_dict.get('quantity', 1)
            unit_price = spare_dict.get('unit_price', 0.0)
            spare_dict['total_cost'] = round(quantity * unit_price, 2)
            
            total_cost += spare_dict['total_cost']
            spares_with_costs.append(spare_dict)
        
        return {
            "total_spare_cost": round(total_cost, 2),
            "spares_used": spares_with_costs
        }
    except Exception as e:
        logger.error(f"Error calculating spare costs: {e}")
        return {"total_spare_cost": 0.0, "spares_used": []}

def check_supabase():
    """Check if Supabase is connected"""
    if supabase is None:
        raise HTTPException(status_code=500, detail="Database connection not available")
    return supabase

def parse_spares(record: dict) -> list:
    """Parse spares_used field from string to list"""
    spares = record.get('spares_used', '[]')
    if isinstance(spares, str):
        try:
            return json.loads(spares)
        except:
            return []
    return spares

# ===== API ENDPOINTS =====

@router.get("/", dependencies=[Depends(get_current_user)])
async def breakdowns_root():
    """Root endpoint"""
    return {
        "message": "Breakdowns Management API",
        "status": "operational",
        "endpoints": {
            "get_breakdowns": "GET /get-breakdowns",
            "create_breakdown": "POST /",
            "get_by_id": "GET /{id}",
            "update": "PATCH /{id}",
            "delete": "DELETE /{id}",
            "dashboard": "GET /dashboard/overview"
        }
    }

@router.get("/get-breakdowns", dependencies=[Depends(get_current_user)])
async def get_breakdowns(
    status: Optional[str] = Query(None),
    breakdown_type: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Get breakdowns with filtering"""
    db = check_supabase()

    cache_key = build_key(
        "breakdowns", _fn="get_breakdowns", status=status, breakdown_type=breakdown_type,
        department=department, limit=limit, offset=offset,
    )
    cached_result = await cache_get(cache_key)
    if cached_result is not None:
        return cached_result

    try:
        query = db.table("breakdowns").select("*")
        
        if status and status != "all":
            query = query.eq("status", status)
        if breakdown_type and breakdown_type != "all":
            query = query.eq("breakdown_type", breakdown_type)
        if department and department != "all":
            query = query.eq("department", department)
        
        response = query.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        records = response.data or []
        # Parse spares_used JSON string back to list
        for record in records:
            if isinstance(record.get('spares_used'), str):
                try:
                    record['spares_used'] = json.loads(record['spares_used'])
                except:
                    record['spares_used'] = []
        
        result = {
            "data": records,
            "count": len(records),
            "success": True
        }
        await cache_set(cache_key, result, ttl=60, namespace="breakdowns")
        return result

    except Exception as e:
        logger.error(f"Error fetching breakdowns: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching breakdowns: {str(e)}")

@router.post("/")
async def create_breakdown(breakdown: BreakdownCreate, current_user: dict = Depends(get_current_user)):
    """Create a new breakdown record"""
    db = check_supabase()
    
    try:
        # Convert to dict
        data = breakdown.dict()
        
        # Calculate time metrics (only returns columns that exist in database)
        time_metrics = calculate_time_metrics(data)
        data.update(time_metrics)
        
        # Calculate spare costs and convert to JSON string
        if data.get('spares_used'):
            costs = calculate_spare_costs(breakdown.spares_used)
            data['total_spare_cost'] = costs['total_spare_cost']
            data['spares_used'] = json.dumps(costs['spares_used'])
        else:
            data['spares_used'] = '[]'
            data['total_spare_cost'] = 0.0
        
        # Add timestamps
        now = datetime.utcnow().isoformat()
        data["created_at"] = now
        data["updated_at"] = now
        
        # Remove any fields that might not exist in the database
        # Ensure we're only sending columns that exist
        expected_columns = [
            'machine_id', 'machine_name', 'machine_description', 'artisan_name',
            'department', 'location', 'breakdown_date', 'breakdown_type',
            'work_done', 'artisan_recommendations', 'status', 'priority',
            'breakdown_start', 'breakdown_end', 'work_start', 'work_end',
            'response_time_minutes', 'repair_time_minutes', 'downtime_minutes',
            'net_downtime_minutes', 'total_spare_cost', 'spares_used',
            'created_at', 'updated_at', 'breakdown_description'
        ]
        
        # Filter out any unexpected keys
        data = {k: v for k, v in data.items() if k in expected_columns}
        
        logger.info(f"Inserting breakdown with {len(data)} fields")
        
        # Insert into database
        response = db.table("breakdowns").insert(data).execute()
        
        if response.data:
            result = response.data[0]
            # Parse spares_used back to list for response
            if isinstance(result.get('spares_used'), str):
                try:
                    result['spares_used'] = json.loads(result['spares_used'])
                except:
                    result['spares_used'] = []
            
            await invalidate_namespace("breakdowns")
            return {
                "success": True,
                "data": result,
                "message": "Breakdown created successfully"
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to create breakdown")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating breakdown: {str(e)}")

@router.get("/{breakdown_id}", dependencies=[Depends(get_current_user)])
async def get_breakdown(breakdown_id: str):
    """Get breakdown by ID"""
    db = check_supabase()

    try:
        response = db.table("breakdowns").select("*").eq("id", breakdown_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Breakdown not found")
        
        result = response.data[0]
        # Parse spares_used
        if isinstance(result.get('spares_used'), str):
            try:
                result['spares_used'] = json.loads(result['spares_used'])
            except:
                result['spares_used'] = []
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching breakdown: {str(e)}")

@router.patch("/{breakdown_id}")
async def update_breakdown(breakdown_id: str, breakdown_update: BreakdownUpdate, current_user: dict = Depends(get_current_user)):
    """Update breakdown"""
    db = check_supabase()

    try:
        # Check if exists
        existing = db.table("breakdowns").select("*").eq("id", breakdown_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Breakdown not found")
        
        # Get update data
        update_data = breakdown_update.dict(exclude_unset=True)
        
        # Handle description field mapping
        if 'breakdown_description' in update_data:
            update_data['machine_description'] = update_data['breakdown_description']
        
        # Handle spares_used
        if 'spares_used' in update_data:
            if update_data['spares_used']:
                costs = calculate_spare_costs(update_data['spares_used'])
                update_data['total_spare_cost'] = costs['total_spare_cost']
                update_data['spares_used'] = json.dumps(costs['spares_used'])
            else:
                update_data['spares_used'] = '[]'
                update_data['total_spare_cost'] = 0.0
        
        # Calculate time metrics if time fields are updated
        if any(key in update_data for key in ['breakdown_start', 'breakdown_end', 'work_start', 'work_end']):
            # Get full data to calculate metrics
            full_data = existing.data[0].copy()
            full_data.update(update_data)
            time_metrics = calculate_time_metrics(full_data)
            update_data.update(time_metrics)
        
        # Update timestamp
        update_data['updated_at'] = datetime.utcnow().isoformat()
        
        # Remove any unexpected fields
        expected_columns = [
            'machine_id', 'machine_name', 'machine_description', 'artisan_name',
            'department', 'location', 'breakdown_date', 'breakdown_type',
            'work_done', 'artisan_recommendations', 'status', 'priority',
            'breakdown_start', 'breakdown_end', 'work_start', 'work_end',
            'response_time_minutes', 'repair_time_minutes', 'downtime_minutes',
            'net_downtime_minutes', 'total_spare_cost', 'spares_used',
            'updated_at', 'breakdown_description'
        ]
        
        update_data = {k: v for k, v in update_data.items() if k in expected_columns}
        
        # Update in database
        response = db.table("breakdowns").update(update_data).eq("id", breakdown_id).execute()
        
        if response.data:
            result = response.data[0]
            # Parse spares_used
            if isinstance(result.get('spares_used'), str):
                try:
                    result['spares_used'] = json.loads(result['spares_used'])
                except:
                    result['spares_used'] = []

            await invalidate_namespace("breakdowns")
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating breakdown: {str(e)}")

@router.delete("/{breakdown_id}")
async def delete_breakdown(breakdown_id: str, current_user: dict = Depends(require_role('manager'))):
    """Delete breakdown"""
    db = check_supabase()

    try:
        # Check if exists
        existing = db.table("breakdowns").select("*").eq("id", breakdown_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Breakdown not found")
        
        # Delete
        db.table("breakdowns").delete().eq("id", breakdown_id).execute()
        await invalidate_namespace("breakdowns")

        return {"success": True, "message": "Breakdown deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting breakdown: {str(e)}")

@router.get("/dashboard/overview")
@cached("breakdowns", ttl=60)
async def get_dashboard_overview():
    """Get dashboard metrics"""
    db = check_supabase()
    
    try:
        response = db.table("breakdowns").select("*").execute()
        records = response.data or []
        
        total = len(records)
        open_count = sum(1 for r in records if r.get('status') in ['logged', 'in_progress'])
        high_priority = sum(1 for r in records if r.get('priority') == 'high')
        critical_priority = sum(1 for r in records if r.get('priority') == 'critical')

        # Get today's breakdowns
        now = datetime.utcnow()
        today = now.strftime('%Y-%m-%d')
        today_breakdowns = sum(1 for r in records if r.get('breakdown_date') == today)

        # Calculate today's downtime
        today_downtime = sum(r.get('downtime_minutes', 0) for r in records if r.get('breakdown_date') == today)

        # This week's breakdowns (Monday through today, UTC)
        week_start = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        week_breakdowns = sum(1 for r in records if r.get('breakdown_date', '') >= week_start)

        return {
            "metrics": {
                "total_breakdowns": total,
                "week_breakdowns": week_breakdowns,
                "open_breakdowns": open_count,
                "high_priority": high_priority,
                "critical_priority": critical_priority,
                "today_breakdowns": today_breakdowns,
                "today_downtime_minutes": today_downtime,
                "today_downtime_hours": round(today_downtime / 60, 2)
            },
            "last_updated": datetime.utcnow().isoformat(),
            "success": True
        }
        
    except Exception as e:
        logger.error(f"Error fetching dashboard: {e}")
        # Return basic mock data
        return {
            "metrics": {
                "total_breakdowns": 0,
                "week_breakdowns": 0,
                "open_breakdowns": 0,
                "high_priority": 0,
                "critical_priority": 0,
                "today_breakdowns": 0,
                "today_downtime_minutes": 0,
                "today_downtime_hours": 0
            },
            "last_updated": datetime.utcnow().isoformat(),
            "success": True,
            "note": "Using fallback data"
        }

@router.get("/health/check")
async def health_check():
    """Health check"""
    try:
        if supabase is None:
            return {
                "status": "unhealthy",
                "database": "not_connected",
                "message": "Supabase client not initialized"
            }
        
        # Test connection
        result = supabase.table("breakdowns").select("id", count="exact").limit(1).execute()
        
        return {
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "database": "connection_failed",
            "error": str(e)
        }

@router.get("/analytics/heatmap", dependencies=[Depends(get_current_user)])
async def get_breakdown_heatmap(
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    department: Optional[str] = Query(None),
    machine_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    breakdown_type: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
):
    """Get comprehensive breakdown analytics with multiple visualizations"""
    db = check_supabase()

    try:
        query = db.table("breakdowns").select("*")

        # Apply filters — kept in sync with the Records tab's Filters bar (status/type/
        # priority/location/department/dates) so switching a filter there also scopes
        # this heatmap; free-text search isn't replicated here (would need ILIKE across
        # several columns to exactly match the client-side search).
        if date_from:
            query = query.gte("breakdown_date", date_from)
        if date_to:
            query = query.lte("breakdown_date", date_to)
        if department and department != "all":
            query = query.eq("department", department)
        if machine_id and machine_id != "all":
            query = query.eq("machine_id", machine_id)
        if status and status != "all":
            query = query.eq("status", status)
        if breakdown_type and breakdown_type != "all":
            query = query.eq("breakdown_type", breakdown_type)
        if priority and priority != "all":
            query = query.eq("priority", priority)
        if location and location != "all":
            query = query.eq("location", location)
        
        response = query.execute()
        records = response.data or []
        
        # ===== Initialize data structures =====
        hour_day_heatmap = [[0 for _ in range(7)] for _ in range(24)]  # 24 hours x 7 days
        machine_breakdowns = {}
        artisan_breakdowns = {}
        spare_parts_usage = {}
        hourly_breakdowns = [0] * 24
        daily_breakdowns = [0] * 7
        
        # NEW: Richer heatmap structures
        breakdown_type_counts = defaultdict(int)
        breakdown_type_hour_heatmap = defaultdict(lambda: [0] * 24)  # type -> [count per hour]
        breakdown_type_day_heatmap = defaultdict(lambda: [0] * 7)    # type -> [count per day]
        department_hour_heatmap = defaultdict(lambda: [0] * 24)      # dept -> [count per hour]
        department_day_heatmap = defaultdict(lambda: [0] * 7)        # dept -> [count per day]
        priority_hour_heatmap = defaultdict(lambda: [0] * 24)        # priority -> [count per hour]
        priority_day_heatmap = defaultdict(lambda: [0] * 7)          # priority -> [count per day]
        response_time_heatmap = [[0 for _ in range(7)] for _ in range(24)]  # avg response time by hour x day
        response_time_count_hm = [[0 for _ in range(7)] for _ in range(24)]  # count for averaging
        monthly_day_heatmap = defaultdict(lambda: [0] * 31)          # month -> [count per day of month]
        artisan_hour_heatmap = defaultdict(lambda: [0] * 24)         # artisan -> [count per hour]
        location_hour_heatmap = defaultdict(lambda: [0] * 24)        # location -> [count per hour]
        
        priority_counts = defaultdict(int)
        status_counts = defaultdict(int)
        department_counts = defaultdict(lambda: {'count': 0, 'downtime': 0})
        monthly_trends = defaultdict(int)
        weekly_trends = defaultdict(int)
        response_time_by_hour = defaultdict(list)
        downtime_by_machine = defaultdict(list)
        repair_time_by_artisan = defaultdict(list)
        location_counts = defaultdict(int)
        machine_type_breakdowns = defaultdict(lambda: {'count': 0, 'downtime': 0})
        
        for record in records:
            # Parse breakdown_start time
            start_time = record.get('breakdown_start', '')
            breakdown_date = record.get('breakdown_date', '')
            
            # Extract hour and day of week
            hour = 0
            day_of_week = 0
            
            if start_time:
                try:
                    time_parts = start_time.split(':')
                    hour = int(time_parts[0]) if time_parts else 0
                except:
                    hour = 0
            
            if breakdown_date:
                try:
                    date_obj = datetime.strptime(breakdown_date, '%Y-%m-%d')
                    day_of_week = date_obj.weekday()  # 0=Monday, 6=Sunday
                    
                    # Monthly trends
                    month_key = date_obj.strftime('%Y-%m')
                    monthly_trends[month_key] += 1
                    
                    # Weekly trends
                    week_key = date_obj.strftime('%Y-W%W')
                    weekly_trends[week_key] += 1
                    
                except:
                    day_of_week = 0
            
            # Fields read by several sections below — extracted once up front so
            # every section (including the hour/day heatmaps, which run first)
            # sees them already defined, instead of relying on a later section
            # to have assigned them first.
            dept = record.get('department', 'Unknown')
            loc = record.get('location', 'Unknown')
            artisan_name = record.get('artisan_name', 'Unknown')
            resp_time = record.get('response_time_minutes', 0)

            # Update heatmap
            if 0 <= hour < 24 and 0 <= day_of_week < 7:
                hour_day_heatmap[hour][day_of_week] += 1

            # Track hourly breakdowns
            hourly_breakdowns[hour] += 1

            # Track daily breakdowns
            daily_breakdowns[day_of_week] += 1

            # Track breakdown type
            btype = record.get('breakdown_type', 'Unknown')
            breakdown_type_counts[btype] += 1

            # Track priority
            priority = record.get('priority', 'medium')
            priority_counts[priority] += 1

            # Richer heatmaps: type x hour/day
            if 0 <= hour < 24:
                breakdown_type_hour_heatmap[btype][hour] += 1
                department_hour_heatmap[dept][hour] += 1
                priority_hour_heatmap[priority][hour] += 1
                artisan_hour_heatmap[artisan_name][hour] += 1
                location_hour_heatmap[loc][hour] += 1
            if 0 <= day_of_week < 7:
                breakdown_type_day_heatmap[btype][day_of_week] += 1
                department_day_heatmap[dept][day_of_week] += 1
                priority_day_heatmap[priority][day_of_week] += 1
            
            # Response time heatmap (avg response time by hour x day)
            if resp_time > 0 and 0 <= hour < 24 and 0 <= day_of_week < 7:
                response_time_heatmap[hour][day_of_week] += resp_time
                response_time_count_hm[hour][day_of_week] += 1
            
            # Monthly day-of-month heatmap
            if breakdown_date:
                try:
                    date_obj = datetime.strptime(breakdown_date, '%Y-%m-%d')
                    day_of_month = date_obj.day - 1  # 0-30
                    if 0 <= day_of_month < 31:
                        monthly_day_heatmap[month_key][day_of_month] += 1
                except:
                    pass
            
            # Track status
            status = record.get('status', 'unknown')
            status_counts[status] += 1
            
            # Track department
            department_counts[dept]['count'] += 1
            department_counts[dept]['downtime'] += record.get('downtime_minutes', 0)

            # Track location
            location_counts[loc] += 1
            
            # Track machine breakdowns
            machine_name = record.get('machine_name', 'Unknown')
            if machine_name not in machine_breakdowns:
                machine_breakdowns[machine_name] = {
                    'count': 0,
                    'total_downtime': 0,
                    'department': record.get('department', 'Unknown'),
                    'total_repair_time': 0,
                    'avg_response_time': 0,
                    'response_times': []
                }
            machine_breakdowns[machine_name]['count'] += 1
            machine_breakdowns[machine_name]['total_downtime'] += record.get('downtime_minutes', 0)
            machine_breakdowns[machine_name]['total_repair_time'] += record.get('repair_time_minutes', 0)
            if resp_time > 0:
                machine_breakdowns[machine_name]['response_times'].append(resp_time)
            
            # Track downtime by machine for scatter
            downtime_by_machine[machine_name].append(record.get('downtime_minutes', 0))
            
            # Track artisan breakdowns
            if artisan_name not in artisan_breakdowns:
                artisan_breakdowns[artisan_name] = {
                    'count': 0,
                    'total_repair_time': 0,
                    'avg_repair_time': 0,
                    'repair_times': []
                }
            artisan_breakdowns[artisan_name]['count'] += 1
            repair_mins = record.get('repair_time_minutes', 0)
            artisan_breakdowns[artisan_name]['total_repair_time'] += repair_mins
            if repair_mins > 0:
                artisan_breakdowns[artisan_name]['repair_times'].append(repair_mins)
            
            # Track response time by hour
            if resp_time > 0:
                response_time_by_hour[hour].append(resp_time)
            
            # Track repair time by artisan
            if repair_mins > 0:
                repair_time_by_artisan[artisan_name].append(repair_mins)
            
            # Track spare parts usage
            spares_used = parse_spares(record)
            
            for spare in spares_used:
                spare_name = spare.get('name', 'Unknown')
                if spare_name not in spare_parts_usage:
                    spare_parts_usage[spare_name] = {
                        'count': 0,
                        'total_cost': 0,
                        'part_number': spare.get('part_number', ''),
                        'total_quantity': 0
                    }
                qty = spare.get('quantity', 1)
                spare_parts_usage[spare_name]['count'] += 1  # times used in breakdowns
                spare_parts_usage[spare_name]['total_quantity'] += qty
                spare_parts_usage[spare_name]['total_cost'] += spare.get('total_cost', 0)
        
        # ===== Compute averages and derived metrics =====
        
        # Machine averages
        for m_name, m_data in machine_breakdowns.items():
            if m_data['response_times']:
                m_data['avg_response_time'] = round(sum(m_data['response_times']) / len(m_data['response_times']), 1)
            m_data['avg_downtime'] = round(m_data['total_downtime'] / m_data['count'], 1) if m_data['count'] > 0 else 0
            m_data['avg_repair_time'] = round(m_data['total_repair_time'] / m_data['count'], 1) if m_data['count'] > 0 else 0
            del m_data['response_times']
        
        # Artisan averages
        for a_name, a_data in artisan_breakdowns.items():
            if a_data['repair_times']:
                a_data['avg_repair_time'] = round(sum(a_data['repair_times']) / len(a_data['repair_times']), 1)
            del a_data['repair_times']
        
        # ===== Sort and format results =====
        top_machines = sorted(
            [{'name': k, **v} for k, v in machine_breakdowns.items()],
            key=lambda x: x['count'],
            reverse=True
        )[:15]
        
        top_artisans = sorted(
            [{'name': k, **v} for k, v in artisan_breakdowns.items()],
            key=lambda x: x['count'],
            reverse=True
        )[:15]
        
        top_spares = sorted(
            [{'name': k, **v} for k, v in spare_parts_usage.items()],
            key=lambda x: x['count'],
            reverse=True
        )[:15]
        
        # Department comparison
        dept_comparison = sorted(
            [{'department': k, **v} for k, v in department_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Breakdown type distribution
        type_distribution = sorted(
            [{'type': k, 'count': v} for k, v in breakdown_type_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Priority distribution
        priority_distribution = sorted(
            [{'priority': k, 'count': v} for k, v in priority_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Status distribution
        status_distribution = sorted(
            [{'status': k, 'count': v} for k, v in status_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Monthly trends (sorted by date)
        monthly_trends_sorted = sorted(
            [{'month': k, 'count': v} for k, v in monthly_trends.items()],
            key=lambda x: x['month']
        )
        
        # Weekly trends (sorted)
        weekly_trends_sorted = sorted(
            [{'week': k, 'count': v} for k, v in weekly_trends.items()],
            key=lambda x: x['week']
        )
        
        # Location distribution
        location_distribution = sorted(
            [{'location': k, 'count': v} for k, v in location_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )
        
        # Response time by hour (average)
        response_time_by_hour_avg = []
        for h in range(24):
            times = response_time_by_hour.get(h, [])
            avg = round(sum(times) / len(times), 1) if times else 0
            response_time_by_hour_avg.append({
                'hour': f'{h:02d}:00',
                'avg_response_time': avg,
                'count': len(times)
            })
        
        # Downtime analysis by machine (for scatter plot)
        machine_downtime_scatter = [
            {
                'name': name,
                'breakdowns': data['count'],
                'total_downtime': data['total_downtime'],
                'avg_downtime': data['avg_downtime'],
                'avg_repair_time': data['avg_repair_time'],
                'department': data['department']
            }
            for name, data in machine_breakdowns.items()
        ]
        
        # Artisan performance comparison
        artisan_performance = sorted(
            [{'name': k, **v} for k, v in artisan_breakdowns.items()],
            key=lambda x: x['count'],
            reverse=True
        )[:15]
        
        # ===== Compute richer heatmap data =====
        # Average response time heatmap
        response_time_heatmap_avg = []
        for h in range(24):
            row = []
            for d in range(7):
                count = response_time_count_hm[h][d]
                total = response_time_heatmap[h][d]
                row.append(round(total / count, 1) if count > 0 else 0)
            response_time_heatmap_avg.append(row)
        
        # Breakdown type x hour heatmap (formatted)
        type_hour_heatmap_formatted = {}
        for btype, hours_list in breakdown_type_hour_heatmap.items():
            type_hour_heatmap_formatted[btype] = [
                {'hour': f'{h:02d}:00', 'count': hours_list[h]}
                for h in range(24)
            ]
        
        # Breakdown type x day heatmap
        type_day_heatmap_formatted = {}
        for btype, days_list in breakdown_type_day_heatmap.items():
            type_day_heatmap_formatted[btype] = [
                {'day': DAY_NAMES[d], 'count': days_list[d]}
                for d in range(7)
            ]
        
        # Department x hour heatmap (top depts only)
        dept_hour_heatmap_formatted = {}
        for dept, hours_list in department_hour_heatmap.items():
            dept_hour_heatmap_formatted[dept] = [
                {'hour': f'{h:02d}:00', 'count': hours_list[h]}
                for h in range(24)
            ]
        
        # Priority x hour heatmap
        priority_hour_heatmap_formatted = {}
        for pri, hours_list in priority_hour_heatmap.items():
            priority_hour_heatmap_formatted[pri] = [
                {'hour': f'{h:02d}:00', 'count': hours_list[h]}
                for h in range(24)
            ]
        
        # Artisan x hour heatmap (top 5 artisans)
        top_artisan_names = [a['name'] for a in artisan_performance[:5]]
        artisan_hour_heatmap_formatted = {}
        for aname in top_artisan_names:
            if aname in artisan_hour_heatmap:
                artisan_hour_heatmap_formatted[aname] = [
                    {'hour': f'{h:02d}:00', 'count': artisan_hour_heatmap[aname][h]}
                    for h in range(24)
                ]
        
        # Location x hour heatmap (top 5 locations)
        top_location_names = [loc for loc, _ in sorted(location_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        location_hour_heatmap_formatted = {}
        for lname in top_location_names:
            if lname in location_hour_heatmap:
                location_hour_heatmap_formatted[lname] = [
                    {'hour': f'{h:02d}:00', 'count': location_hour_heatmap[lname][h]}
                    for h in range(24)
                ]
        
        # Monthly day-of-month heatmap (formatted)
        monthly_day_heatmap_formatted = {}
        for month, days_list in monthly_day_heatmap.items():
            monthly_day_heatmap_formatted[month] = [
                {'day': d + 1, 'count': days_list[d]}
                for d in range(31)
            ]
        
        return {
            "heatmap": {
                "hour_day": hour_day_heatmap,
                "labels": {
                    "hours": [f"{h:02d}:00" for h in range(24)],
                    "days": DAY_NAMES
                }
            },
            "hourly_distribution": [
                {"hour": f"{h:02d}:00", "count": hourly_breakdowns[h]}
                for h in range(24)
            ],
            "daily_distribution": [
                {"day": DAY_NAMES[d], "count": daily_breakdowns[d]}
                for d in range(7)
            ],
            "top_problem_machines": top_machines,
            "top_artisans": top_artisans,
            "top_spare_parts": top_spares,
            # NEW analytics sections
            "breakdown_type_distribution": type_distribution,
            "priority_distribution": priority_distribution,
            "status_distribution": status_distribution,
            "department_comparison": dept_comparison,
            "monthly_trends": monthly_trends_sorted,
            "weekly_trends": weekly_trends_sorted,
            "location_distribution": location_distribution,
            "response_time_by_hour": response_time_by_hour_avg,
            "machine_downtime_scatter": machine_downtime_scatter,
            "artisan_performance": artisan_performance,
            "summary": {
                "total_breakdowns": len(records),
                "unique_machines": len(machine_breakdowns),
                "unique_artisans": len(artisan_breakdowns),
                "unique_spares": len(spare_parts_usage),
                "unique_departments": len(department_counts),
                "unique_types": len(breakdown_type_counts),
                "total_downtime_minutes": sum(r.get('downtime_minutes', 0) for r in records),
                "total_repair_time_minutes": sum(r.get('repair_time_minutes', 0) for r in records),
                "total_spare_cost": sum(r.get('total_spare_cost', 0) for r in records)
            },
            # Richer heatmap data
            "response_time_heatmap": response_time_heatmap_avg,
            "type_hour_heatmap": type_hour_heatmap_formatted,
            "type_day_heatmap": type_day_heatmap_formatted,
            "dept_hour_heatmap": dept_hour_heatmap_formatted,
            "priority_hour_heatmap": priority_hour_heatmap_formatted,
            "artisan_hour_heatmap": artisan_hour_heatmap_formatted,
            "location_hour_heatmap": location_hour_heatmap_formatted,
            "monthly_day_heatmap": monthly_day_heatmap_formatted,
            "filters_applied": {
                "date_from": date_from,
                "date_to": date_to,
                "department": department,
                "machine_id": machine_id
            },
            "success": True
        }
        
    except Exception as e:
        logger.error(f"Error generating heatmap analytics: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating analytics: {str(e)}")