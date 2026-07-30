# app/routers/compressors.py
# Complete Compressor Tracking System Backend API with Supabase Integration

import os
import json
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from enum import Enum
from collections import defaultdict

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Depends, Query, Body, BackgroundTasks, Form, UploadFile, File, Request
from app.uploads import read_and_validate_upload
from app.rate_limit import limiter
from app.aggregation import count_by
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import asyncio
from contextlib import asynccontextmanager
import numpy as np
import pandas as pd
import io
import csv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Database table names - MAKE SURE THESE MATCH YOUR SUPABASE TABLES
COMPRESSORS_TABLE = "compressors"
READINGS_TABLE = "compressor_readings"
DAILY_ENTRIES_TABLE = "daily_entries"
SERVICE_RECORDS_TABLE = "service_records"
MAINTENANCE_SCHEDULE_TABLE = "maintenance_schedule"
ALERTS_TABLE = "alerts"
SERVICE_INTERVALS_TABLE = "service_intervals"

# Create router
router = APIRouter()

# Enums
class CompressorStatus(str, Enum):
    RUNNING = "running"
    STANDBY = "standby"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"

class ServiceUrgency(str, Enum):
    CRITICAL = "critical"    # Due now or overdue
    HIGH = "high"            # Due in 0-7 days
    MEDIUM = "medium"        # Due in 8-30 days
    LOW = "low"             # Due in 31+ days
    COMPLETED = "completed"  # Service completed

class AlertType(str, Enum):
    MAINTENANCE_DUE = "maintenance_due"
    EFFICIENCY_LOW = "efficiency_low"
    PRESSURE_HIGH = "pressure_high"
    TEMPERATURE_HIGH = "temperature_high"
    HOURS_EXCEEDED = "hours_exceeded"
    SYSTEM = "system"

# Pydantic Models
class CompressorBase(BaseModel):
    name: str
    model: str
    capacity: str
    status: CompressorStatus = CompressorStatus.STANDBY
    location: str
    color: str = "bg-blue-500"
    initial_total_running: float = 0.0  # Starting point for calculations
    initial_total_loaded: float = 0.0   # Starting point for calculations
    total_running_hours: float = 0.0
    total_loaded_hours: float = 0.0
    manufacturer: Optional[str] = None
    serial_number: Optional[str] = None
    installation_date: Optional[str] = None
    last_service_date: Optional[str] = None
    notes: Optional[str] = None

class CompressorCreate(CompressorBase):
    pass

class CompressorUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    capacity: Optional[str] = None
    status: Optional[CompressorStatus] = None
    location: Optional[str] = None
    color: Optional[str] = None
    initial_total_running: Optional[float] = None
    initial_total_loaded: Optional[float] = None
    total_running_hours: Optional[float] = None
    total_loaded_hours: Optional[float] = None
    last_service_date: Optional[str] = None
    notes: Optional[str] = None

class DailyUpdateRequest(BaseModel):
    compressor_id: str
    date: str
    current_total_running: float
    current_total_loaded: float
    pressure: float = 0.0
    temperature: float = 0.0
    notes: Optional[str] = None

class ServiceRecordCreate(BaseModel):
    compressor_id: str
    service_type: str
    service_date: str
    running_hours_at_service: float = 0.0
    description: Optional[str] = None
    is_completed: bool = False

class StatusUpdateRequest(BaseModel):
    status: CompressorStatus

# Supabase client — this router used to build its OWN client with a hardcoded
# fallback service-role key (a real credential, sitting in source) and would
# silently swap in a fake in-memory "mock" client if the connection failed,
# which could make broken DB connectivity look like "it worked, there's just
# no data" instead of failing loudly. Use the single shared client every other
# router uses instead — one client, one set of credentials, no hidden mock.
from app.supabase_client import supabase as _shared_supabase_client
from app.auth import get_current_user, require_role

def get_supabase():
    return _shared_supabase_client

# Helper functions
def calculate_efficiency(running_hours: float, loaded_hours: float) -> float:
    """Calculate efficiency percentage"""
    if running_hours == 0:
        return 0.0
    efficiency = (loaded_hours / running_hours) * 100
    # Cap efficiency at 100%
    return min(round(efficiency, 1), 100.0)

def get_service_urgency(hours_until_service: float, avg_daily_hours: float = 8.0) -> ServiceUrgency:
    """Determine service urgency based on days remaining"""
    days_remaining = hours_until_service / avg_daily_hours
    
    if days_remaining <= 0:
        return ServiceUrgency.CRITICAL
    elif days_remaining <= 7:
        return ServiceUrgency.HIGH
    elif days_remaining <= 30:
        return ServiceUrgency.MEDIUM
    else:
        return ServiceUrgency.LOW

def generate_service_intervals(current_hours: float) -> List[int]:
    """Generate upcoming service intervals"""
    intervals = [1000, 2000, 4000, 8000, 16000]
    return [interval for interval in intervals if interval > current_hours]

def calculate_daily_hours(previous_total: float, current_total: float) -> float:
    """Calculate daily hours from cumulative totals"""
    daily = current_total - previous_total
    daily = max(daily, 0)  # Ensure non-negative
    return round(daily, 2)

def validate_date_format(date_str: str) -> bool:
    """Validate date format (YYYY-MM-DD)"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def ensure_valid_daily_hours(daily_running: float, daily_loaded: float, 
                           current_total_loaded: float, previous_total_loaded: float):
    """Ensure daily loaded hours don't exceed daily running hours, adjusting totals if needed"""
    # Ensure loaded doesn't exceed running
    if daily_loaded > daily_running:
        daily_loaded = daily_running
    
    # Ensure values are non-negative
    daily_running = max(daily_running, 0)
    daily_loaded = max(daily_loaded, 0)
    
    # Recalculate total loaded to maintain consistency
    current_total_loaded = previous_total_loaded + daily_loaded
    
    return daily_running, daily_loaded, current_total_loaded

async def recalculate_subsequent_readings(compressor_id: str, from_date: str, supabase_client):
    """Recalculate all readings after the specified date"""
    try:
        # Get all readings after the specified date, sorted by date
        subsequent_readings = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .gt("date", from_date)\
            .order("date", asc=True)\
            .execute()
        
        if not subsequent_readings.data:
            return
        
        readings = subsequent_readings.data
        
        for i, reading in enumerate(readings):
            current_date = reading["date"]
            
            # Get previous reading
            if i == 0:
                # First subsequent reading - get the reading at from_date
                previous_reading = supabase_client.table(READINGS_TABLE)\
                    .select("*")\
                    .eq("compressor_id", compressor_id)\
                    .eq("date", from_date)\
                    .execute()
                
                if previous_reading.data and len(previous_reading.data) > 0:
                    prev = previous_reading.data[0]
                    previous_total_running = prev["total_running_hours"]
                    previous_total_loaded = prev["total_loaded_hours"]
                else:
                    # If no reading at from_date, get the latest before it
                    previous_reading = supabase_client.table(READINGS_TABLE)\
                        .select("*")\
                        .eq("compressor_id", compressor_id)\
                        .lt("date", from_date)\
                        .order("date", desc=True)\
                        .limit(1)\
                        .execute()
                    
                    if previous_reading.data and len(previous_reading.data) > 0:
                        prev = previous_reading.data[0]
                        previous_total_running = prev["total_running_hours"]
                        previous_total_loaded = prev["total_loaded_hours"]
                    else:
                        continue
            else:
                # Use the previous reading in our list
                prev = readings[i-1]
                previous_total_running = prev["total_running_hours"]
                previous_total_loaded = prev["total_loaded_hours"]
            
            # Calculate daily hours
            daily_running = reading["total_running_hours"] - previous_total_running
            daily_loaded = reading["total_loaded_hours"] - previous_total_loaded
            
            # Ensure valid values
            daily_running = max(daily_running, 0)
            daily_loaded = max(daily_loaded, 0)
            
            # Ensure loaded doesn't exceed running
            if daily_loaded > daily_running:
                daily_loaded = daily_running
            
            # Calculate efficiency
            efficiency = calculate_efficiency(daily_running, daily_loaded)
            
            # Update reading
            supabase_client.table(READINGS_TABLE)\
                .update({
                    "daily_running_hours": daily_running,
                    "daily_loaded_hours": daily_loaded,
                    "efficiency": efficiency,
                    "updated_at": datetime.utcnow().isoformat()
                })\
                .eq("id", reading["id"])\
                .execute()
            
    except Exception as e:
        logger.error(f"Error recalculating subsequent readings: {str(e)}")

# API Routes

@router.get("/", dependencies=[Depends(get_current_user)])
async def root():
    return {
        "message": "Compressor Tracking System API",
        "status": "online", 
        "version": "2.0.0",
        "endpoints": [
            "/api/compressors/compressors - GET/POST compressors",
            "/api/compressors/stats - GET system statistics",
            "/api/compressors/service-due - GET upcoming services",
            "/api/compressors/daily-entries/cumulative - POST daily entries",
            "/docs - API documentation"
        ]
    }

@router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

# Compressor CRUD Operations
@router.get("/compressors", response_model=List[Dict[str, Any]], dependencies=[Depends(get_current_user)])
async def get_compressors(
    status: Optional[str] = None,
    location: Optional[str] = None,
    supabase_client = Depends(get_supabase)
):
    """Get all compressors with optional filtering"""
    try:
        query = supabase_client.table(COMPRESSORS_TABLE).select("*")
        
        if status:
            query = query.eq("status", status)
        if location:
            query = query.eq("location", location)
        
        result = query.order("created_at", desc=False).execute()
        
        # Return empty array if no data
        compressors = result.data if result.data else []
        
        # Add calculated efficiency for each compressor
        for compressor in compressors:
            running = compressor.get("total_running_hours", 0.0)
            loaded = compressor.get("total_loaded_hours", 0.0)
            compressor["efficiency"] = calculate_efficiency(running, loaded)
        
        return compressors
    except Exception as e:
        logger.error(f"Error fetching compressors: {str(e)}")
        # Return empty array instead of throwing error for frontend
        return []

@router.get("/compressors/{compressor_id}", response_model=Dict[str, Any], dependencies=[Depends(get_current_user)])
async def get_compressor_by_id(
    compressor_id: str,
    supabase_client = Depends(get_supabase)
):
    """Get a specific compressor by ID"""
    try:
        result = supabase_client.table(COMPRESSORS_TABLE)\
            .select("*")\
            .eq("id", compressor_id)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="Compressor not found")
        
        compressor = result.data[0]
        running = compressor.get("total_running_hours", 0.0)
        loaded = compressor.get("total_loaded_hours", 0.0)
        compressor["efficiency"] = calculate_efficiency(running, loaded)
        
        return compressor
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching compressor: {str(e)}")

@router.post("/compressors", response_model=Dict[str, Any])
async def create_compressor(
    compressor: CompressorCreate,
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(get_current_user)):
    """Create a new compressor"""
    try:
        compressor_data = compressor.dict()
        compressor_data["id"] = str(uuid.uuid4())
        compressor_data["created_at"] = datetime.utcnow().isoformat()
        compressor_data["updated_at"] = datetime.utcnow().isoformat()
        
        # Set initial totals if not provided
        if compressor_data.get("initial_total_running") is None:
            compressor_data["initial_total_running"] = compressor_data.get("total_running_hours", 0.0)
        if compressor_data.get("initial_total_loaded") is None:
            compressor_data["initial_total_loaded"] = compressor_data.get("total_loaded_hours", 0.0)
        
        result = supabase_client.table(COMPRESSORS_TABLE).insert(compressor_data).execute()
        
        if hasattr(result, 'data') and result.data:
            return {
                "success": True, 
                "data": result.data[0], 
                "message": "Compressor created successfully"
            }
        else:
            return {
                "success": True, 
                "data": compressor_data, 
                "message": "Compressor created successfully (mock)"
            }
    except Exception as e:
        logger.error(f"Error creating compressor: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating compressor: {str(e)}")

@router.put("/compressors/{compressor_id}", response_model=Dict[str, Any])
async def update_compressor(
    compressor_id: str,
    compressor_update: CompressorUpdate,
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(get_current_user)):
    """Update a compressor"""
    try:
        update_data = compressor_update.dict(exclude_unset=True)
        update_data["updated_at"] = datetime.utcnow().isoformat()
        
        result = supabase_client.table(COMPRESSORS_TABLE).update(update_data).eq("id", compressor_id).execute()
        
        if hasattr(result, 'data') and result.data:
            return {
                "success": True, 
                "data": result.data[0], 
                "message": "Compressor updated successfully"
            }
        else:
            return {
                "success": True, 
                "message": "Compressor updated successfully"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating compressor: {str(e)}")

@router.patch("/compressors/{compressor_id}/status", response_model=Dict[str, Any])
async def update_compressor_status(
    compressor_id: str,
    status_update: StatusUpdateRequest,
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(get_current_user)):
    """Update only the status of a compressor"""
    try:
        new_status = status_update.status.value
        
        # Update only the status
        update_data = {
            "status": new_status,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        result = supabase_client.table(COMPRESSORS_TABLE)\
            .update(update_data)\
            .eq("id", compressor_id)\
            .execute()
        
        if hasattr(result, 'data') and result.data:
            return {
                "success": True,
                "data": result.data[0],
                "message": f"Compressor status updated to {new_status}"
            }
        else:
            return {
                "success": True,
                "message": "Compressor status updated"
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating compressor status: {str(e)}")

@router.delete("/compressors/{compressor_id}")
async def delete_compressor(
    compressor_id: str,
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(require_role('manager'))):
    """Delete a compressor"""
    try:
        supabase_client.table(COMPRESSORS_TABLE).delete().eq("id", compressor_id).execute()
        return {"success": True, "message": "Compressor deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting compressor: {str(e)}")

# Daily Entry Operations
@router.post("/daily-entries/cumulative", response_model=Dict[str, Any])
async def create_daily_entry_cumulative(
    request: DailyUpdateRequest,
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(get_current_user)):
    """Create or update daily entry using cumulative hours with proper validation"""
    try:
        date_str = request.date
        compressor_id = request.compressor_id
        
        # Validate date format
        if not validate_date_format(date_str):
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        # Get compressor info
        compressor_result = supabase_client.table(COMPRESSORS_TABLE)\
            .select("*")\
            .eq("id", compressor_id)\
            .execute()
        
        if not compressor_result.data or len(compressor_result.data) == 0:
            raise HTTPException(status_code=404, detail="Compressor not found")
        
        compressor = compressor_result.data[0]
        
        # Get ALL readings for this compressor, sorted by date
        all_readings = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .order("date", desc=True)\
            .execute()
        
        readings = all_readings.data if all_readings.data else []
        
        # Find existing reading for this date
        existing_reading = None
        for reading in readings:
            if reading["date"] == date_str:
                existing_reading = reading
                break
        
        # Find previous reading (latest reading before this date)
        previous_reading = None
        for reading in readings:
            reading_date = datetime.strptime(reading["date"], "%Y-%m-%d").date()
            if reading_date < date_obj:
                previous_reading = reading
                break
        
        # Calculate daily hours
        if previous_reading:
            # We have a previous reading - calculate difference
            previous_total_running = previous_reading["total_running_hours"]
            previous_total_loaded = previous_reading["total_loaded_hours"]
            
            daily_running = request.current_total_running - previous_total_running
            daily_loaded = request.current_total_loaded - previous_total_loaded
            
            # Validate: Current totals must be >= previous totals
            if request.current_total_running < previous_total_running:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Current running hours ({request.current_total_running}) cannot be less than previous reading's total ({previous_total_running})"
                )
            if request.current_total_loaded < previous_total_loaded:
                raise HTTPException(
                    status_code=400,
                    detail=f"Current loaded hours ({request.current_total_loaded}) cannot be less than previous reading's total ({previous_total_loaded})"
                )
            
        else:
            # No previous reading - use compressor's initial totals
            previous_total_running = compressor.get("initial_total_running", 0.0) or 0.0
            previous_total_loaded = compressor.get("initial_total_loaded", 0.0) or 0.0
            
            daily_running = request.current_total_running - previous_total_running
            daily_loaded = request.current_total_loaded - previous_total_loaded
            
            # Validate first reading
            if request.current_total_running < previous_total_running:
                raise HTTPException(
                    status_code=400,
                    detail=f"Initial running hours ({request.current_total_running}) cannot be less than initial total ({previous_total_running})"
                )
            if request.current_total_loaded < previous_total_loaded:
                raise HTTPException(
                    status_code=400,
                    detail=f"Initial loaded hours ({request.current_total_loaded}) cannot be less than initial total ({previous_total_loaded})"
                )
        
        # Ensure valid daily hours (non-negative and loaded <= running)
        daily_running, daily_loaded, request.current_total_loaded = ensure_valid_daily_hours(
            daily_running, daily_loaded, 
            request.current_total_loaded, previous_total_loaded
        )
        
        # Calculate efficiency
        efficiency = calculate_efficiency(daily_running, daily_loaded)
        
        # Create reading entry
        reading_data = {
            "id": existing_reading["id"] if existing_reading else str(uuid.uuid4()),
            "compressor_id": compressor_id,
            "date": date_str,
            "total_running_hours": request.current_total_running,
            "total_loaded_hours": request.current_total_loaded,
            "daily_running_hours": daily_running,
            "daily_loaded_hours": daily_loaded,
            "efficiency": efficiency,
            "pressure": request.pressure,
            "temperature": request.temperature,
            "notes": request.notes,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # If there's an existing reading, also add created_at
        if existing_reading:
            reading_data["created_at"] = existing_reading["created_at"]
        else:
            reading_data["created_at"] = datetime.utcnow().isoformat()
        
        # Save to database
        try:
            if existing_reading:
                # Update existing reading
                result = supabase_client.table(READINGS_TABLE)\
                    .update(reading_data)\
                    .eq("id", existing_reading["id"])\
                    .execute()
            else:
                # Insert new reading
                result = supabase_client.table(READINGS_TABLE).insert(reading_data).execute()
        except Exception as db_error:
            error_str = str(db_error)
            if "violates check constraint" in error_str:
                if "chk_daily_loaded_positive" in error_str:
                    # Fix the constraint issue by adjusting loaded hours
                    if daily_loaded < 0:
                        daily_loaded = 0
                        request.current_total_loaded = previous_total_loaded
                    
                    # Recalculate with adjusted values
                    daily_running, daily_loaded, request.current_total_loaded = ensure_valid_daily_hours(
                        daily_running, daily_loaded, 
                        request.current_total_loaded, previous_total_loaded
                    )
                    efficiency = calculate_efficiency(daily_running, daily_loaded)
                    
                    reading_data.update({
                        "daily_loaded_hours": daily_loaded,
                        "total_loaded_hours": request.current_total_loaded,
                        "efficiency": efficiency,
                        "updated_at": datetime.utcnow().isoformat()
                    })
                    
                    # Retry the operation
                    if existing_reading:
                        result = supabase_client.table(READINGS_TABLE)\
                            .update(reading_data)\
                            .eq("id", existing_reading["id"])\
                            .execute()
                    else:
                        result = supabase_client.table(READINGS_TABLE).insert(reading_data).execute()
                else:
                    raise HTTPException(status_code=400, detail=f"Database constraint error: {error_str}")
            else:
                raise
        
        # Update compressor's latest totals if this is the most recent reading
        latest_reading = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .order("date", desc=True)\
            .limit(1)\
            .execute()
        
        if latest_reading.data and len(latest_reading.data) > 0:
            latest = latest_reading.data[0]
            if latest["date"] == date_str:
                update_result = supabase_client.table(COMPRESSORS_TABLE)\
                    .update({
                        "total_running_hours": request.current_total_running,
                        "total_loaded_hours": request.current_total_loaded,
                        "updated_at": datetime.utcnow().isoformat()
                    })\
                    .eq("id", compressor_id)\
                    .execute()
        
        # Check for service due
        await check_and_update_service_due(compressor_id, request.current_total_running, supabase_client)
        
        return {
            "success": True,
            "data": reading_data,
            "message": "Daily entry saved successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating daily entry: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating daily entry: {str(e)}")

@router.get("/readings/{compressor_id}", dependencies=[Depends(get_current_user)])
async def get_compressor_readings(
    compressor_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    supabase_client = Depends(get_supabase)
):
    """Get all readings for a specific compressor"""
    try:
        query = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .order("date", asc=True)
        
        if start_date:
            query = query.gte("date", start_date)
        if end_date:
            query = query.lte("date", end_date)
        
        result = query.execute()
        
        return {
            "success": True,
            "data": result.data if result.data else [],
            "count": len(result.data) if result.data else 0
        }
    except Exception as e:
        logger.error(f"Error fetching readings: {str(e)}")
        return {"success": False, "data": [], "error": str(e)}

@router.get("/readings/{compressor_id}/detailed", dependencies=[Depends(get_current_user)])
async def get_detailed_readings(
    compressor_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    supabase_client = Depends(get_supabase)
):
    """Get detailed readings with calculated daily values"""
    try:
        query = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .order("date", asc=True)
        
        if start_date:
            query = query.gte("date", start_date)
        if end_date:
            query = query.lte("date", end_date)
        
        result = query.execute()
        
        if not result.data:
            return {
                "success": True,
                "data": [],
                "message": "No readings found"
            }
        
        # Get compressor initial totals
        compressor_result = supabase_client.table(COMPRESSORS_TABLE)\
            .select("initial_total_running, initial_total_loaded")\
            .eq("id", compressor_id)\
            .execute()
        
        initial_running = 0.0
        initial_loaded = 0.0
        if compressor_result.data and len(compressor_result.data) > 0:
            initial_running = compressor_result.data[0].get("initial_total_running", 0.0) or 0.0
            initial_loaded = compressor_result.data[0].get("initial_total_loaded", 0.0) or 0.0
        
        # Add calculated totals
        readings = result.data
        cumulative_running = initial_running
        cumulative_loaded = initial_loaded
        
        for reading in readings:
            # Add calculated values
            reading["cumulative_running"] = cumulative_running + reading.get("daily_running_hours", 0)
            reading["cumulative_loaded"] = cumulative_loaded + reading.get("daily_loaded_hours", 0)
            
            # Update running totals
            cumulative_running = reading["cumulative_running"]
            cumulative_loaded = reading["cumulative_loaded"]
        
        total_running_hours = sum(r.get("daily_running_hours", 0) for r in readings)
        total_loaded_hours = sum(r.get("daily_loaded_hours", 0) for r in readings)
        
        return {
            "success": True,
            "data": readings,
            "count": len(readings),
            "initial_running_hours": initial_running,
            "initial_loaded_hours": initial_loaded,
            "total_running_hours": total_running_hours,
            "total_loaded_hours": total_loaded_hours,
            "overall_efficiency": calculate_efficiency(total_running_hours, total_loaded_hours) if total_running_hours > 0 else 0
        }
    except Exception as e:
        logger.error(f"Error fetching detailed readings: {str(e)}")
        return {"success": False, "data": [], "error": str(e)}

@router.get("/readings/date/{date}", dependencies=[Depends(get_current_user)])
async def get_readings_by_date(
    date: str,
    supabase_client = Depends(get_supabase)
):
    """Get all readings for a specific date"""
    try:
        if not validate_date_format(date):
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        result = supabase_client.table(READINGS_TABLE)\
            .select("*")\
            .eq("date", date)\
            .execute()
        
        return {
            "success": True,
            "data": result.data if result.data else [],
            "count": len(result.data) if result.data else 0
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching readings by date: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching readings: {str(e)}")

async def check_and_update_service_due(compressor_id: str, current_hours: float, supabase_client):
    """Check if service is due and update maintenance schedule"""
    try:
        # Get compressor info
        compressor_result = supabase_client.table(COMPRESSORS_TABLE)\
            .select("name")\
            .eq("id", compressor_id)\
            .execute()
        
        if not compressor_result.data:
            return
        
        compressor_name = compressor_result.data[0].get("name", "Unknown")
        
        # Get service intervals
        intervals_result = supabase_client.table(SERVICE_INTERVALS_TABLE)\
            .select("*")\
            .order("interval_hours")\
            .execute()
        
        if not intervals_result.data:
            return
        
        intervals = intervals_result.data
        
        # Find next service interval
        next_interval = None
        for interval in intervals:
            if interval["interval_hours"] > current_hours:
                next_interval = interval
                break
        
        if not next_interval:
            return
        
        hours_remaining = next_interval["interval_hours"] - current_hours
        days_remaining = max(0, int(hours_remaining / 8))  # Assuming 8 hours/day
        
        # Determine urgency
        if days_remaining <= 0:
            urgency = "critical"
        elif days_remaining <= 7:
            urgency = "high"
        elif days_remaining <= 30:
            urgency = "medium"
        else:
            urgency = "low"
        
        # Update or create maintenance schedule
        maintenance_data = {
            "compressor_id": compressor_id,
            "service_type": f"{next_interval['interval_hours']} Hour Service",
            "service_interval_hours": next_interval["interval_hours"],
            "next_service_date": (datetime.now() + timedelta(days=days_remaining)).date().isoformat(),
            "estimated_service_date": (datetime.now() + timedelta(days=days_remaining)).date().isoformat(),
            "urgency": urgency,
            "is_active": True,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # Check if maintenance schedule already exists
        existing_schedule = supabase_client.table(MAINTENANCE_SCHEDULE_TABLE)\
            .select("*")\
            .eq("compressor_id", compressor_id)\
            .eq("service_interval_hours", next_interval["interval_hours"])\
            .execute()
        
        if existing_schedule.data and len(existing_schedule.data) > 0:
            # Update existing
            supabase_client.table(MAINTENANCE_SCHEDULE_TABLE)\
                .update(maintenance_data)\
                .eq("compressor_id", compressor_id)\
                .eq("service_interval_hours", next_interval["interval_hours"])\
                .execute()
        else:
            # Create new
            maintenance_data["id"] = str(uuid.uuid4())
            maintenance_data["created_at"] = datetime.utcnow().isoformat()
            supabase_client.table(MAINTENANCE_SCHEDULE_TABLE).insert(maintenance_data).execute()
        
        # Create alert if urgent
        if urgency in ["critical", "high"]:
            alert_data = {
                "id": str(uuid.uuid4()),
                "compressor_id": compressor_id,
                "alert_type": "maintenance_due",
                "title": f"Service Due for {compressor_name}",
                "message": f"{compressor_name} is due for {next_interval['interval_hours']} hour service in {days_remaining} days. Current hours: {current_hours}",
                "severity": "critical" if urgency == "critical" else "warning",
                "is_read": False,
                "is_resolved": False,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            supabase_client.table(ALERTS_TABLE).insert(alert_data).execute()
            
    except Exception as e:
        logger.error(f"Error checking service due: {str(e)}")

# Statistics
@router.get("/stats", dependencies=[Depends(get_current_user)])
async def get_stats(supabase_client = Depends(get_supabase)):
    """Get system statistics"""
    try:
        # Get all compressors
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []
        
        if not compressors:
            return {
                "total_compressors": 0,
                "total_running_hours": 0.0,
                "total_loaded_hours": 0.0,
                "avg_efficiency": 0.0,
                "active_compressors": 0,
                "upcoming_services": 0,
                "urgent_alerts": 0
            }
        
        total_compressors = len(compressors)
        total_running_hours = sum(c.get("total_running_hours", 0) for c in compressors)
        total_loaded_hours = sum(c.get("total_loaded_hours", 0) for c in compressors)
        
        # Calculate active compressors (running or standby)
        active_compressors = sum(1 for c in compressors if c.get("status") in ["running", "standby"])
        
        # Calculate average efficiency from recent readings
        all_efficiencies = []
        for compressor in compressors:
            # For simplicity, calculate efficiency from totals
            running = compressor.get("total_running_hours", 0)
            loaded = compressor.get("total_loaded_hours", 0)
            if running > 0:
                all_efficiencies.append((loaded / running) * 100)
        
        avg_efficiency = round(sum(all_efficiencies) / len(all_efficiencies), 1) if all_efficiencies else 0.0
        
        # Calculate upcoming services
        upcoming_services = 0
        for compressor in compressors:
            current_hours = compressor.get("total_running_hours", 0)
            intervals = generate_service_intervals(current_hours)
            if intervals:
                next_service = intervals[0]
                hours_remaining = next_service - current_hours
                if hours_remaining <= 240:  # 30 days at 8 hours/day
                    upcoming_services += 1
        
        return {
            "total_compressors": total_compressors,
            "total_running_hours": round(total_running_hours, 1),
            "total_loaded_hours": round(total_loaded_hours, 1),
            "avg_efficiency": avg_efficiency,
            "active_compressors": active_compressors,
            "upcoming_services": upcoming_services,
            "urgent_alerts": 0  # Placeholder
        }
    except Exception as e:
        logger.error(f"Error calculating statistics: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error calculating statistics: {str(e)}")

@router.get("/service-due", dependencies=[Depends(get_current_user)])
async def get_service_due(supabase_client = Depends(get_supabase)):
    """Get compressors with upcoming services"""
    try:
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []
        
        service_due_list = []
        
        for compressor in compressors:
            current_hours = compressor.get("total_running_hours", 0)
            service_intervals = generate_service_intervals(current_hours)
            
            if not service_intervals:
                continue
            
            next_service = service_intervals[0]
            hours_remaining = next_service - current_hours
            days_remaining = max(0, int(hours_remaining / 8))  # Assuming 8 hours/day
            
            urgency_level = get_service_urgency(hours_remaining)
            
            service_due_list.append({
                "compressor_id": compressor["id"],
                "compressor_name": compressor["name"],
                "current_hours": current_hours,
                "next_service_hours": next_service,
                "hours_remaining": hours_remaining,
                "days_remaining": days_remaining,
                "urgency": urgency_level.value,
                "service_interval": next_service
            })
        
        # Sort by urgency
        urgency_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        service_due_list.sort(key=lambda x: urgency_order.get(x["urgency"], 4))
        
        return service_due_list
    except Exception as e:
        logger.error(f"Error checking service due: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error checking service due: {str(e)}")

# Analytics endpoints
@router.get("/analytics/performance-metrics", dependencies=[Depends(get_current_user)])
async def get_performance_metrics(
    period_days: int = Query(30, description="Period in days"),
    supabase_client = Depends(get_supabase)
):
    """Get performance metrics"""
    try:
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []

        metrics = []
        for compressor in compressors:
            # Get readings for this compressor
            readings_result = supabase_client.table(READINGS_TABLE)\
                .select("*")\
                .eq("compressor_id", compressor["id"])\
                .order("date", desc=True)\
                .execute()
            
            readings = readings_result.data if readings_result.data else []
            
            if not readings:
                # No data yet for this compressor
                metrics.append({
                    "compressor_id": compressor["id"],
                    "compressor_name": compressor["name"],
                    "avg_efficiency": 0.0,
                    "avg_daily_running_hours": 0.0,
                    "avg_daily_loaded_hours": 0.0,
                    "total_running_hours": compressor.get("total_running_hours", 0),
                    "total_loaded_hours": compressor.get("total_loaded_hours", 0),
                    "avg_pressure": 0.0,
                    "avg_temperature": 0.0,
                    "downtime_percentage": 0.0,
                    "service_count": 0
                })
                continue
            
            # Calculate metrics from actual readings
            total_readings = len(readings)
            total_running = sum(r.get("daily_running_hours", 0) for r in readings)
            total_loaded = sum(r.get("daily_loaded_hours", 0) for r in readings)
            efficiencies = [r.get("efficiency", 0) for r in readings if r.get("efficiency", 0) > 0]
            
            avg_efficiency = sum(efficiencies) / len(efficiencies) if efficiencies else 0
            avg_daily_running = total_running / total_readings if total_readings > 0 else 0
            avg_daily_loaded = total_loaded / total_readings if total_readings > 0 else 0
            
            # Calculate downtime (days with 0 running hours)
            zero_running_days = sum(1 for r in readings if r.get("daily_running_hours", 0) == 0)
            downtime_percentage = (zero_running_days / total_readings * 100) if total_readings > 0 else 0
            
            metrics.append({
                "compressor_id": compressor["id"],
                "compressor_name": compressor["name"],
                "avg_efficiency": round(avg_efficiency, 1),
                "avg_daily_running_hours": round(avg_daily_running, 1),
                "avg_daily_loaded_hours": round(avg_daily_loaded, 1),
                "total_running_hours": compressor.get("total_running_hours", 0),
                "total_loaded_hours": compressor.get("total_loaded_hours", 0),
                "avg_pressure": 0.0,  # Will be calculated when we have pressure data
                "avg_temperature": 0.0,  # Will be calculated when we have temperature data
                "downtime_percentage": round(downtime_percentage, 1),
                "service_count": 0  # Will be calculated when we have service data
            })
        
        return metrics
    except Exception as e:
        logger.error(f"Error getting performance metrics: {str(e)}")
        return []

@router.get("/analytics/trends", dependencies=[Depends(get_current_user)])
async def get_trend_analysis(
    period: str = Query('monthly', description="Period for trends: monthly, weekly, quarterly"),
    supabase_client = Depends(get_supabase)
):
    """Get trend analysis data"""
    try:
        # Check if we have any readings at all
        readings_result = supabase_client.table(READINGS_TABLE).select("*").limit(1).execute()
        
        if not readings_result.data or len(readings_result.data) == 0:
            # Return proper structure with empty array and success message
            return {
                "success": True,
                "data": [],
                "message": "No trend data available yet. Data will appear after daily entries.",
                "has_data": False
            }
        
        # If there's data, implement trend analysis
        # Get all compressors
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []
        
        trends = []
        
        for compressor in compressors:
            # Get readings for this compressor
            readings_result = supabase_client.table(READINGS_TABLE)\
                .select("*")\
                .eq("compressor_id", compressor["id"])\
                .order("date", desc=True)\
                .execute()
            
            readings = readings_result.data if readings_result.data else []
            
            if not readings or len(readings) < 2:
                continue  # Need at least 2 readings for trend analysis
            
            # Calculate trend metrics
            if len(readings) >= 7:  # Enough data for weekly trend
                recent = readings[:7]
                older = readings[7:14] if len(readings) >= 14 else []
                
                if older:
                    recent_efficiency = sum(r.get("efficiency", 0) for r in recent) / len(recent)
                    older_efficiency = sum(r.get("efficiency", 0) for r in older) / len(older)
                    
                    efficiency_trend = "stable"
                    if recent_efficiency > older_efficiency + 5:
                        efficiency_trend = "improving"
                    elif recent_efficiency < older_efficiency - 5:
                        efficiency_trend = "declining"
                    
                    trends.append({
                        "compressor_id": compressor["id"],
                        "compressor_name": compressor["name"],
                        "period": period,
                        "avg_efficiency": round(recent_efficiency, 1),
                        "total_running_hours": sum(r.get("daily_running_hours", 0) for r in recent),
                        "total_loaded_hours": sum(r.get("daily_loaded_hours", 0) for r in recent),
                        "efficiency_trend": efficiency_trend,
                        "has_data": True
                    })
        
        return {
            "success": True,
            "data": trends,
            "message": f"Found {len(trends)} trends" if trends else "No trend data available",
            "has_data": len(trends) > 0
        }
        
    except Exception as e:
        logger.error(f"Error getting trend analysis: {str(e)}")
        return {
            "success": False,
            "data": [],
            "message": f"Error getting trend analysis: {str(e)}",
            "has_data": False
        }

@router.get("/analytics/comparison", dependencies=[Depends(get_current_user)])
async def get_comparison_analytics(
    metric: str = Query('efficiency', description="Metric for comparison: efficiency, running_hours, loaded_hours"),
    supabase_client = Depends(get_supabase)
):
    """Get comparison analytics for compressors"""
    try:
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []

        comparison_data = []

        for compressor in compressors:
            running = compressor.get("total_running_hours", 0)
            loaded = compressor.get("total_loaded_hours", 0)
            
            if metric == 'efficiency':
                value = calculate_efficiency(running, loaded) if running > 0 else 0
            elif metric == 'running_hours':
                value = running
            elif metric == 'loaded_hours':
                value = loaded
            else:
                value = 0

            # Determine rating based on value
            if metric == 'efficiency':
                if value >= 80:
                    rating = "Excellent"
                elif value >= 60:
                    rating = "Good"
                elif value >= 40:
                    rating = "Fair"
                else:
                    rating = "Poor"
            else:
                # For hours metrics
                if value > 2000:
                    rating = "Very High"
                elif value > 1000:
                    rating = "High"
                elif value > 500:
                    rating = "Medium"
                else:
                    rating = "Low"

            comparison_data.append({
                "compressor_id": compressor["id"],
                "compressor_name": compressor["name"],
                "location": compressor.get("location", "Unknown"),
                "value": round(value, 1),
                "rating": rating
            })

        # Sort by value in descending order
        comparison_data.sort(key=lambda x: x["value"], reverse=True)

        return {
            "success": True,
            "data": comparison_data,
            "message": f"Comparison data for {metric}",
            "count": len(comparison_data)
        }

    except Exception as e:
        logger.error(f"Error getting comparison analytics: {str(e)}")
        return {
            "success": False,
            "data": [],
            "message": f"Error getting comparison analytics: {str(e)}",
            "count": 0
        }

@router.get("/management/summary", dependencies=[Depends(get_current_user)])
async def get_management_summary(supabase_client = Depends(get_supabase)):
    """Get management summary"""
    try:
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []
        
        # Calculate statistics
        status_counts = count_by(compressors, 'status', default='unknown')
        location_counts = count_by(compressors, 'location', default='Unknown')
        
        return {
            "success": True,
            "total_compressors": len(compressors),
            "status_distribution": status_counts,
            "location_distribution": location_counts,
            "total_hours_by_location": {},
            "age_distribution": {
                "less_than_year": len(compressors),
                "1_3_years": 0,
                "3_5_years": 0,
                "more_than_5": 0
            },
            "unread_alerts": 0,
            "recent_alerts": [],
            "recent_services": [],
            "message": "Management summary loaded successfully"
        }
    except Exception as e:
        logger.error(f"Error getting management summary: {str(e)}")
        return {
            "success": False,
            "message": f"Error getting management summary: {str(e)}"
        }

# Export/Import endpoints
@router.post("/export")
async def export_data(
    supabase_client = Depends(get_supabase),
    current_user: dict = Depends(get_current_user),
):
    """Export data to CSV format"""
    try:
        # Get all compressors data
        compressors_result = supabase_client.table(COMPRESSORS_TABLE).select("*").execute()
        compressors = compressors_result.data if compressors_result.data else []
        
        # Create CSV content
        csv_content = "Name,Model,Capacity,Status,Location,Total Running Hours,Total Loaded Hours,Efficiency\n"
        
        for compressor in compressors:
            running = compressor.get("total_running_hours", 0)
            loaded = compressor.get("total_loaded_hours", 0)
            efficiency = calculate_efficiency(running, loaded)
            
            csv_content += f"{compressor.get('name', '')},{compressor.get('model', '')},"
            csv_content += f"{compressor.get('capacity', '')},{compressor.get('status', '')},"
            csv_content += f"{compressor.get('location', '')},{running},{loaded},{efficiency}\n"
        
        # Create response
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=compressor_export.csv",
                "Content-Type": "text/csv"
            }
        )
        
    except Exception as e:
        logger.error(f"Error exporting data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error exporting data: {str(e)}")

@router.post("/import")
@limiter.limit("10/minute")
async def import_data(
    request: Request,
    file: UploadFile = File(...),
    supabase_client = Depends(get_supabase)
, current_user: dict = Depends(get_current_user)):
    """Import data from CSV file"""
    try:
        contents = await read_and_validate_upload(file, max_bytes=10 * 1024 * 1024, allowed_exts={"csv"})
        try:
            content_str = contents.decode('utf-8')
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="File is not valid UTF-8 text — expected a CSV file")

        # Parse CSV content
        lines = content_str.strip().split('\n')
        headers = lines[0].split(',')
        
        imported_count = 0
        errors = []
        
        for i, line in enumerate(lines[1:], start=2):  # Skip header
            try:
                values = line.split(',')
                if len(values) >= 6:
                    compressor_data = {
                        "id": str(uuid.uuid4()),
                        "name": values[0].strip(),
                        "model": values[1].strip(),
                        "capacity": values[2].strip(),
                        "status": values[3].strip().lower() if values[3].strip().lower() in ['running', 'standby', 'maintenance', 'offline'] else 'standby',
                        "location": values[4].strip(),
                        "total_running_hours": float(values[5].strip()) if len(values) > 5 and values[5].strip() else 0.0,
                        "total_loaded_hours": float(values[6].strip()) if len(values) > 6 and values[6].strip() else 0.0,
                        "initial_total_running": float(values[5].strip()) if len(values) > 5 and values[5].strip() else 0.0,
                        "initial_total_loaded": float(values[6].strip()) if len(values) > 6 and values[6].strip() else 0.0,
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    
                    # Insert into database
                    supabase_client.table(COMPRESSORS_TABLE).insert(compressor_data).execute()
                    imported_count += 1
                    
            except Exception as line_error:
                errors.append(f"Line {i}: {str(line_error)}")
        
        return {
            "success": True,
            "imported_count": imported_count,
            "errors": errors,
            "message": f"Imported {imported_count} compressors successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error importing data: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error importing data: {str(e)}")

@router.post("/service-records")
async def create_service_record(
    service_record: ServiceRecordCreate,
    supabase_client = Depends(get_supabase),
    current_user: dict = Depends(get_current_user),
):
    """Create a service record"""
    try:
        # Add metadata
        data = service_record.dict()
        data["id"] = str(uuid.uuid4())
        data["created_at"] = datetime.utcnow().isoformat()

        # Insert into database
        result = supabase_client.table(SERVICE_RECORDS_TABLE).insert(data).execute()

        return {
            "success": True,
            "data": data,
            "message": "Service record created successfully"
        }
        
    except Exception as e:
        logger.error(f"Error creating service record: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating service record: {str(e)}")

@router.get("/test")
async def test_endpoint():
    return {"message": "Compressors API is working", "timestamp": datetime.utcnow().isoformat()}

# Function to initialize data on startup
async def on_startup():
    """Initialize compressors data on startup"""
    try:
        supabase_client = get_supabase()
        # No mock data - system starts clean
        logger.info("Compressors system ready - starting with clean database")
    except Exception as e:
        logger.error(f"Error during compressors startup: {e}")