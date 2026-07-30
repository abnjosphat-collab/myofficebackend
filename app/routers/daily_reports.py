# backend/app/routers/daily_report.py
from fastapi import APIRouter, HTTPException, Query, Request, Depends
from app.auth import get_current_user, require_role
from app.db_helpers import apply_date_range
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json
import logging
from supabase import create_client, Client
import os
from dotenv import load_dotenv
import traceback

# Load environment variables
load_dotenv()

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI router
router = APIRouter()

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Supabase client initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Supabase: {e}")
        supabase = None
else:
    logger.warning("⚠️ Supabase credentials not found. Running in local mode.")

# Pydantic Models
class CallOut(BaseModel):
    shift: str = "day"
    description: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    duration_hours: float = 0

class EquipmentPerformance(BaseModel):
    name: str
    category: str
    target: float = 100
    actual: Optional[float] = None

class DailyReportCreate(BaseModel):
    date: str
    safety: str = ""
    projects: str = ""
    weekly_plan: str = ""
    daily_checks: str = ""
    power_availability: str = "normal"
    dam_level: Optional[float] = 0
    plant_availability_percent: float = 97
    call_outs: List[CallOut] = []
    equipment: List[EquipmentPerformance] = []
    notes: str = ""

# Helper Functions
def format_supabase_response(data):
    """Format Supabase response"""
    if hasattr(data, 'data'):
        return data.data
    return data

def parse_json_fields(report: dict) -> dict:
    """Parse JSON fields in report"""
    if report.get('call_outs') and isinstance(report['call_outs'], str):
        try:
            report['call_outs'] = json.loads(report['call_outs'])
        except:
            report['call_outs'] = []
    
    if report.get('equipment') and isinstance(report['equipment'], str):
        try:
            report['equipment'] = json.loads(report['equipment'])
        except:
            report['equipment'] = []
    
    return report

# ===== REMOVED DUPLICATE ROOT ENDPOINT =====
# The root endpoint was causing conflict with get_reports
# Both were @router.get("/") - removed one to fix the conflict

# Health check endpoint
@router.get("/health/check")
async def health_check():
    """Check database health"""
    try:
        if not supabase:
            return {
                "status": "connected",
                "report_count": 0,
                "message": "Running in local mode"
            }
        
        # Test connection
        result = supabase.table("daily_reports").select("*", count="exact").limit(1).execute()
        count = result.count if hasattr(result, 'count') else 0
        
        return {
            "status": "healthy",
            "report_count": count,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "status": "error",
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }

# GET reports endpoint - ONLY ONE GET "/" ENDPOINT
@router.get("/", dependencies=[Depends(get_current_user)])
async def get_reports(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    limit: int = Query(1000, ge=1, le=1000)
):
    """Get all daily reports"""
    logger.info(f"📡 GET /api/daily-reports called with start_date={start_date}, end_date={end_date}")
    
    try:
        if not supabase:
            # Return sample data for testing
            return [{
                "id": 1,
                "date": datetime.now().date().isoformat(),
                "safety": "Sample safety observation",
                "projects": "Sample project update",
                "weekly_plan": "Sample weekly plan",
                "daily_checks": "Sample daily checks",
                "power_availability": "normal",
                "dam_level": 7.5,
                "plant_availability_percent": 95.5,
                "call_outs": [],
                "equipment": [],
                "notes": "Sample notes",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }]
        
        # Build query
        query = supabase.table("daily_reports").select("*")
        
        query = apply_date_range(query, "date", start_date, end_date)
        
        # Execute query
        result = query.order("date", desc=True).limit(limit).execute()
        reports = format_supabase_response(result)
        
        logger.info(f"✅ Retrieved {len(reports)} reports")
        
        # Parse JSON fields
        for report in reports:
            parse_json_fields(report)
        
        return reports
        
    except Exception as e:
        logger.error(f"❌ Error in get_reports: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error fetching reports: {str(e)}")

# POST report endpoint - CRITICAL: This was being hidden by duplicate routes
@router.post("/")
async def create_report(report: DailyReportCreate, current_user: dict = Depends(get_current_user)):
    """Create a new daily report"""
    logger.info(f"💾 Creating report for date: {report.date}")
    
    try:
        if not supabase:
            # Return dummy data for testing
            return {
                "id": 1,
                **report.dict(),
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        
        # Check if report already exists
        existing = supabase.table("daily_reports") \
            .select("*") \
            .eq("date", report.date) \
            .execute()
        
        # Prepare data
        report_data = report.dict()
        report_data['call_outs'] = json.dumps(report_data['call_outs'])
        report_data['equipment'] = json.dumps(report_data['equipment'])
        
        now = datetime.now().isoformat()
        
        if existing.data:
            # Update existing
            report_data['updated_at'] = now
            result = supabase.table("daily_reports") \
                .update(report_data) \
                .eq("date", report.date) \
                .execute()
        else:
            # Create new
            report_data['created_at'] = now
            report_data['updated_at'] = now
            result = supabase.table("daily_reports").insert(report_data).execute()
        
        created_data = format_supabase_response(result)
        
        if not created_data:
            raise HTTPException(status_code=500, detail="Failed to save report")
        
        created_report = created_data[0]
        parse_json_fields(created_report)
        
        logger.info(f"✅ Report saved successfully")
        return created_report
        
    except Exception as e:
        logger.error(f"❌ Error in create_report: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error creating report: {str(e)}")

# Stats endpoint
@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats_summary(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    """Get statistics summary"""
    try:
        if not supabase:
            return {
                "total_reports": 1,
                "avg_plant_availability": 95.5,
                "total_callouts": 0,
                "total_callout_hours": 0,
                "avg_dam_level": 7.5
            }
        
        # Get reports
        query = supabase.table("daily_reports").select("*")
        query = apply_date_range(query, "date", start_date, end_date)
        
        result = query.execute()
        reports = format_supabase_response(result)
        
        if not reports:
            return {
                "total_reports": 0,
                "avg_plant_availability": 0,
                "total_callouts": 0,
                "total_callout_hours": 0,
                "avg_dam_level": 0
            }
        
        # Calculate stats
        total_reports = len(reports)
        total_plant = 0
        total_dam = 0
        total_callout_hours = 0
        
        for report in reports:
            total_plant += float(report.get('plant_availability_percent', 0))
            total_dam += float(report.get('dam_level', 0))
            
            # Parse callouts
            callouts = report.get('call_outs', '[]')
            if isinstance(callouts, str):
                try:
                    callouts_list = json.loads(callouts)
                except:
                    callouts_list = []
            else:
                callouts_list = callouts
            
            total_callout_hours += sum(float(co.get('duration_hours', 0)) for co in callouts_list)
        
        return {
            "total_reports": total_reports,
            "avg_plant_availability": round(total_plant / total_reports, 2) if total_reports > 0 else 0,
            "total_callouts": total_reports,  # Simplified for now
            "total_callout_hours": round(total_callout_hours, 2),
            "avg_dam_level": round(total_dam / total_reports, 2) if total_reports > 0 else 0
        }
        
    except Exception as e:
        logger.error(f"❌ Error in get_stats_summary: {str(e)}")
        return {
            "total_reports": 0,
            "avg_plant_availability": 0,
            "total_callouts": 0,
            "total_callout_hours": 0,
            "avg_dam_level": 0
        }

# Trends endpoints
@router.get("/trends/plant-availability", dependencies=[Depends(get_current_user)])
async def get_plant_availability_trend(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    """Get plant availability trend"""
    try:
        if not supabase:
            return {
                "dates": [datetime.now().date().isoformat()],
                "plant_availability": [95.5],
                "dam_levels": [7.5]
            }
        
        # Get reports
        query = supabase.table("daily_reports") \
            .select("date, plant_availability_percent, dam_level") \
            .order("date", desc=True) \
            .limit(30)
        
        query = apply_date_range(query, "date", start_date, end_date)
        
        result = query.execute()
        reports = format_supabase_response(result)
        
        if not reports:
            return {"dates": [], "plant_availability": [], "dam_levels": []}
        
        reports.sort(key=lambda x: x.get('date', ''))
        
        dates = []
        plant_availability = []
        dam_levels = []
        
        for report in reports:
            dates.append(report.get('date'))
            plant_availability.append(float(report.get('plant_availability_percent', 0)))
            dam_levels.append(float(report.get('dam_level', 0)))
        
        return {
            "dates": dates,
            "plant_availability": plant_availability,
            "dam_levels": dam_levels
        }
        
    except Exception as e:
        logger.error(f"❌ Error in plant availability trend: {str(e)}")
        return {"dates": [], "plant_availability": [], "dam_levels": []}

@router.get("/trends/equipment-performance", dependencies=[Depends(get_current_user)])
async def get_equipment_performance_trend(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    """Get equipment performance trend"""
    try:
        if not supabase:
            return {"equipment_data": [], "categories": []}
        
        # Get reports
        query = supabase.table("daily_reports") \
            .select("date, equipment") \
            .order("date", desc=True) \
            .limit(30)
        
        query = apply_date_range(query, "date", start_date, end_date)
        
        result = query.execute()
        reports = format_supabase_response(result)
        
        if not reports:
            return {"equipment_data": [], "categories": []}
        
        # Process equipment data
        equipment_map = {}
        categories = set()
        
        for report in reports:
            equipment = report.get('equipment', '[]')
            if isinstance(equipment, str):
                try:
                    equipment_list = json.loads(equipment)
                except:
                    equipment_list = []
            else:
                equipment_list = equipment
            
            for eq in equipment_list:
                name = eq.get('name')
                category = eq.get('category')
                actual = eq.get('actual', 0)
                
                if not name:
                    continue
                
                if category:
                    categories.add(category)
                
                if name not in equipment_map:
                    equipment_map[name] = {
                        'name': name,
                        'category': category,
                        'performance_data': [],
                        'dates': []
                    }
                
                equipment_map[name]['performance_data'].append(float(actual) if actual else 0)
                equipment_map[name]['dates'].append(report.get('date'))
        
        equipment_data = list(equipment_map.values())
        equipment_data.sort(key=lambda x: len(x['performance_data']), reverse=True)
        
        return {
            "equipment_data": equipment_data[:10],
            "categories": list(categories)
        }
        
    except Exception as e:
        logger.error(f"❌ Error in equipment performance trend: {str(e)}")
        return {"equipment_data": [], "categories": []}

# Delete all reports
@router.delete("/")
async def delete_all_reports(current_user: dict = Depends(require_role('manager'))):
    """Delete all reports"""
    try:
        if not supabase:
            return {
                "success": True,
                "detail": "No database connection",
                "deleted_count": 0
            }
        
        # Get count before deletion
        all_reports = supabase.table("daily_reports").select("*").execute()
        report_count = len(all_reports.data) if all_reports.data else 0
        
        if report_count == 0:
            return {
                "success": True,
                "detail": "No reports to delete",
                "deleted_count": 0
            }
        
        # Delete all
        supabase.table("daily_reports").delete().neq("id", 0).execute()
        
        return {
            "success": True,
            "detail": f"All {report_count} reports deleted",
            "deleted_count": report_count
        }
        
    except Exception as e:
        logger.error(f"❌ Error deleting reports: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting reports: {str(e)}")

# Export endpoints
@router.get("/export/excel", dependencies=[Depends(get_current_user)])
async def export_to_excel(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None)
):
    """Export to Excel - placeholder"""
    return {"message": "Excel export endpoint", "start_date": start_date, "end_date": end_date}

@router.get("/export/pdf/{report_id}", dependencies=[Depends(get_current_user)])
async def export_to_pdf(report_id: int):
    """Export to PDF - placeholder"""
    return {"message": f"PDF export for report {report_id}"}