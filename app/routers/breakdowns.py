# app/routers/breakdowns.py
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
import json
import logging
import os
from supabase import create_client, Client

# Configure logging
logger = logging.getLogger(__name__)

# Initialize router
router = APIRouter(prefix="/api/breakdowns", tags=["breakdowns"])

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

# ===== API ENDPOINTS =====

@router.get("/")
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

@router.get("/get-breakdowns")
async def get_breakdowns(
    status: Optional[str] = Query(None),
    breakdown_type: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Get breakdowns with filtering"""
    db = check_supabase()
    
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
        
        return {
            "data": records,
            "count": len(records),
            "success": True
        }
        
    except Exception as e:
        logger.error(f"Error fetching breakdowns: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching breakdowns: {str(e)}")

@router.post("/")
async def create_breakdown(breakdown: BreakdownCreate):
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

@router.get("/{breakdown_id}")
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
async def update_breakdown(breakdown_id: str, breakdown_update: BreakdownUpdate):
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
            
            return result
        else:
            raise HTTPException(status_code=500, detail="Update failed")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating breakdown: {str(e)}")

@router.delete("/{breakdown_id}")
async def delete_breakdown(breakdown_id: str):
    """Delete breakdown"""
    db = check_supabase()

    try:
        # Check if exists
        existing = db.table("breakdowns").select("*").eq("id", breakdown_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Breakdown not found")
        
        # Delete
        db.table("breakdowns").delete().eq("id", breakdown_id).execute()
        
        return {"success": True, "message": "Breakdown deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting breakdown: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting breakdown: {str(e)}")

@router.get("/dashboard/overview")
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
        today = datetime.utcnow().strftime('%Y-%m-%d')
        today_breakdowns = sum(1 for r in records if r.get('breakdown_date') == today)
        
        # Calculate today's downtime
        today_downtime = sum(r.get('downtime_minutes', 0) for r in records if r.get('breakdown_date') == today)
        
        return {
            "metrics": {
                "total_breakdowns": total,
                "week_breakdowns": total,  # Simplified for now
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