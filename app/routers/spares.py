"""
Spares Management Router - PostgreSQL/Supabase Version
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime, date
from app.supabase_client import supabase
import logging
import json

logger = logging.getLogger(__name__)
router = APIRouter()

# Custom JSON encoder to handle date objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)

# Pydantic Models matching frontend
class SpareCreate(BaseModel):
    stock_code: str = Field(..., min_length=1, description="Unique stock code")
    description: str = Field(..., min_length=1, description="Part description")
    category: Optional[str] = Field(None, description="Primary category (legacy)")
    categories: Optional[List[str]] = Field(default_factory=list, description="Multi-category tags")
    machine_type: Optional[str] = Field(None, description="Machine type")
    current_quantity: int = Field(0, ge=0, description="Current stock quantity")
    min_quantity: int = Field(1, ge=0, description="Minimum stock level")
    max_quantity: int = Field(5, gt=0, description="Maximum stock level")
    unit_price: float = Field(0.0, ge=0, description="Unit price")
    unit_of_measure: Optional[str] = Field("UN", description="Unit of measure (UN, KG, EA, etc)")
    priority: str = Field("medium", description="Priority level")
    storage_location: Optional[str] = Field(None, description="Storage location")
    supplier: Optional[str] = Field(None, description="Supplier name")
    safety_stock: bool = Field(False, description="Safety stock flag")
    notes: Optional[str] = Field(None, description="Additional notes")
    lead_time_days: Optional[int] = Field(0, ge=0, description="Lead time in days")
    last_ordered_date: Optional[str] = Field(None, description="Last ordered date (ISO string)")

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class SpareUpdate(BaseModel):
    stock_code: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = Field(None, min_length=1)
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    machine_type: Optional[str] = None
    current_quantity: Optional[int] = Field(None, ge=0)
    min_quantity: Optional[int] = Field(None, ge=0)
    max_quantity: Optional[int] = Field(None, gt=0)
    unit_price: Optional[float] = Field(None, ge=0)
    unit_of_measure: Optional[str] = None
    priority: Optional[str] = None
    storage_location: Optional[str] = None
    supplier: Optional[str] = None
    safety_stock: Optional[bool] = None
    notes: Optional[str] = None
    lead_time_days: Optional[int] = Field(None, ge=0)
    last_ordered_date: Optional[str] = None

    class Config:
        json_encoders = {
            date: lambda v: v.isoformat(),
            datetime: lambda v: v.isoformat()
        }

class BulkSpareCreate(BaseModel):
    items: List[SpareCreate]
    skip_existing: bool = Field(True, description="Skip items with existing stock codes")

# Helper function to convert dates in records
def convert_dates_to_iso(record):
    """Convert date objects to ISO format strings for JSON serialization"""
    if isinstance(record, dict):
        for key, value in record.items():
            if isinstance(value, (date, datetime)):
                record[key] = value.isoformat()
    return record

# Columns confirmed to exist in the Supabase spares table.
# Add more here once you run: ALTER TABLE spares ADD COLUMN IF NOT EXISTS ...
_DB_COLUMNS = {
    'stock_code', 'description', 'category', 'categories', 'machine_type',
    'current_quantity', 'min_quantity', 'max_quantity', 'unit_price',
    'priority', 'storage_location', 'supplier', 'safety_stock',
    'notes', 'unit_of_measure', 'lead_time_days', 'last_ordered_date',
}

# Helper function to clean data
def clean_data(data: dict) -> dict:
    """Clean data by removing None values and empty strings"""
    cleaned = {}
    for key, value in data.items():
        if value is not None and value != "":
            if isinstance(value, str):
                value = value.strip()
                if value:
                    cleaned[key] = value
            else:
                cleaned[key] = value
    return cleaned

def filter_for_db(data: dict) -> dict:
    """Strip fields that are not yet in the DB schema to avoid PGRST204 errors."""
    return {k: v for k, v in data.items() if k in _DB_COLUMNS}

# GET all spares
@router.get("")
async def get_spares(
    search: Optional[str] = Query(None, description="Search in stock code, description, or notes"),
    category: Optional[str] = Query(None, description="Filter by category (checks both category and categories array)"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    limit: int = Query(5000, ge=1, le=10000, description="Limit results"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
):
    """Get all spares with optional filtering"""
    try:
        query = supabase.table("spares").select("*")

        if search:
            # Search in stock_code, description, and notes
            query = query.or_(
                f"stock_code.ilike.%{search}%,"
                f"description.ilike.%{search}%,"
                f"notes.ilike.%{search}%"
            )

        if category:
            # Filter: matches single category field OR is contained in categories array
            query = query.or_(
                f"category.eq.{category},"
                f"categories.cs.{{\"{category}\"}}"
            )

        if priority:
            query = query.eq("priority", priority)

        # Apply ordering and pagination
        query = query.order("stock_code", desc=False).limit(limit).offset(offset)

        response = query.execute()

        # Convert dates to ISO format for JSON serialization
        records = response.data or []
        for record in records:
            convert_dates_to_iso(record)
            # Ensure categories is always a list
            if 'categories' not in record or record['categories'] is None:
                record['categories'] = []

        return records

    except Exception as e:
        logger.error(f"Error fetching spares: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching spares: {str(e)}")

# GET single spare
@router.get("/{spare_id}")
async def get_spare(spare_id: int):
    """Get a specific spare by ID"""
    try:
        response = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        if 'categories' not in result or result['categories'] is None:
            result['categories'] = []
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching spare: {str(e)}")

# POST bulk create spares
@router.post("/bulk", status_code=201)
async def bulk_create_spares(payload: BulkSpareCreate):
    """Bulk create spare parts, skipping existing stock codes by default"""
    try:
        created = 0
        skipped = 0
        errors = 0

        items = payload.items
        batch_size = 50

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]

            for spare in batch:
                try:
                    if payload.skip_existing:
                        existing = supabase.table("spares").select("id").eq("stock_code", spare.stock_code).execute()
                        if existing.data:
                            skipped += 1
                            continue

                    spare_data = spare.dict()
                    supabase.table("spares").insert(spare_data).execute()
                    created += 1
                except Exception as item_err:
                    logger.warning(f"Skipping {spare.stock_code}: {item_err}")
                    errors += 1

        logger.info(f"Bulk import complete: {created} created, {skipped} skipped, {errors} errors")
        return {"created": created, "skipped": skipped, "errors": errors, "total": len(items)}

    except Exception as e:
        logger.error(f"Bulk create error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Bulk create failed: {str(e)}")


# POST create spare
@router.post("", status_code=201)
async def create_spare(spare: SpareCreate):
    """Create a new spare part"""
    try:
        # Check if stock code already exists
        existing = supabase.table("spares").select("id").eq("stock_code", spare.stock_code).execute()
        
        if existing.data:
            raise HTTPException(status_code=400, detail=f"Stock code '{spare.stock_code}' already exists")
        
        # Insert new spare (filter to known DB columns only)
        response = supabase.table("spares").insert(filter_for_db(spare.dict())).execute()
        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create spare part")
        
        logger.info(f"Created spare part: {spare.stock_code}")
        
        result = response.data[0]
        convert_dates_to_iso(result)
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating spare: {str(e)}")

# PUT update spare
@router.put("/{spare_id}")
async def update_spare(spare_id: int, spare_update: SpareUpdate):
    """Update an existing spare part"""
    try:
        # Check if spare exists
        existing = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        # Check stock code conflict if updating
        if spare_update.stock_code:
            conflict = supabase.table("spares") \
                .select("id") \
                .eq("stock_code", spare_update.stock_code) \
                .neq("id", spare_id) \
                .execute()
            
            if conflict.data:
                raise HTTPException(status_code=400, detail=f"Stock code '{spare_update.stock_code}' already exists")
        
        # Clean and filter update data to known DB columns only
        update_data = filter_for_db(clean_data(spare_update.dict(exclude_unset=True)))
        
        if not update_data:
            raise HTTPException(status_code=400, detail="No data provided for update")
        
        # Update in database
        response = supabase.table("spares").update(update_data).eq("id", spare_id).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update spare part")

        logger.info(f"Updated spare part: {spare_id}")

        # Propagate price change into stock_issues JSONB items so historical
        # records reflect the current catalogue price.
        # Requires this function in Supabase (run once):
        #
        #   CREATE OR REPLACE FUNCTION sync_issue_item_prices(p_stock_code TEXT, p_new_price FLOAT8)
        #   RETURNS INTEGER AS $$
        #   DECLARE updated_count INTEGER := 0;
        #   BEGIN
        #     UPDATE stock_issues
        #     SET items = (
        #       SELECT jsonb_agg(
        #         CASE WHEN item->>'stock_code' = p_stock_code
        #              THEN jsonb_set(item, '{unit_price}', to_jsonb(p_new_price))
        #              ELSE item END
        #       )
        #       FROM jsonb_array_elements(items) AS item
        #     )
        #     WHERE items @> jsonb_build_array(jsonb_build_object('stock_code', p_stock_code));
        #     GET DIAGNOSTICS updated_count = ROW_COUNT;
        #     RETURN updated_count;
        #   END;
        #   $$ LANGUAGE plpgsql SECURITY DEFINER;
        if 'unit_price' in update_data:
            old_stock_code = existing.data[0].get('stock_code')
            try:
                sync_result = supabase.rpc('sync_issue_item_prices', {
                    'p_stock_code': old_stock_code,
                    'p_new_price': float(update_data['unit_price']),
                }).execute()
                updated_issues = sync_result.data or 0
                if updated_issues:
                    logger.info(f"Synced price ${update_data['unit_price']} for {old_stock_code} into {updated_issues} issue record(s)")
            except Exception as sync_err:
                # Non-fatal — function may not exist yet (migration pending)
                logger.warning(f"Price sync to stock_issues skipped: {sync_err}")

        result = response.data[0]
        convert_dates_to_iso(result)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating spare: {str(e)}")

# DELETE spare
@router.delete("/{spare_id}")
async def delete_spare(spare_id: int):
    """Delete a spare part"""
    try:
        # Check if spare exists
        existing = supabase.table("spares").select("*").eq("id", spare_id).execute()
        
        if not existing.data:
            raise HTTPException(status_code=404, detail="Spare part not found")
        
        spare_data = existing.data[0]
        
        # Delete from database
        supabase.table("spares").delete().eq("id", spare_id).execute()
        
        logger.info(f"Deleted spare part: {spare_id} - {spare_data.get('stock_code', 'Unknown')}")
        
        return {
            "message": "Spare part deleted successfully",
            "id": spare_id,
            "stock_code": spare_data.get('stock_code', 'Unknown')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting spare: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting spare: {str(e)}")

# GET suggestions for a field
@router.get("/suggestions/{field}")
async def get_suggestions(field: str):
    """Get unique values for a field for auto-suggestions"""
    allowed_fields = ['category', 'machine_type', 'priority', 'storage_location', 'supplier']
    
    if field not in allowed_fields:
        raise HTTPException(status_code=400, detail=f"Field '{field}' not allowed for suggestions")
    
    try:
        # Get distinct values
        response = supabase.table("spares").select(field).execute()
        
        if not response.data:
            return {"suggestions": []}
        
        # Extract unique non-empty values
        values = set()
        for item in response.data:
            if field in item and item[field] and str(item[field]).strip():
                values.add(str(item[field]).strip())
        
        suggestions = list(sorted(values))
        return {"suggestions": suggestions}
        
    except Exception as e:
        logger.error(f"Error getting suggestions for {field}: {e}")
        return {"suggestions": []}

# GET statistics
@router.get("/stats/summary")
async def get_stats():
    """Get summary statistics"""
    try:
        # Get all spares
        response = supabase.table("spares").select("*").execute()
        
        if not response.data:
            return {
                "total": 0,
                "out_of_stock": 0,
                "low_stock": 0,
                "critical": 0,
                "safety_stock": 0,
                "total_value": 0
            }
        
        spares = response.data
        total = len(spares)
        
        out_of_stock = 0
        low_stock = 0
        critical = 0
        safety_stock = 0
        total_value = 0
        
        for spare in spares:
            current = spare.get('current_quantity', 0) or 0
            min_qty = spare.get('min_quantity', 1) or 1
            price = spare.get('unit_price', 0) or 0
            
            # Calculate inventory value
            total_value += current * price
            
            # Count statuses
            if current <= 0:
                out_of_stock += 1
            elif current <= min_qty:
                low_stock += 1
            
            # Count critical items
            if spare.get('priority') == 'critical':
                critical += 1
            
            # Count safety stock items
            if spare.get('safety_stock') == True:
                safety_stock += 1
        
        return {
            "total": total,
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "critical": critical,
            "safety_stock": safety_stock,
            "total_value": round(total_value, 2)
        }
        
    except Exception as e:
        logger.error(f"Error fetching stats: {str(e)}")
        return {
            "total": 0,
            "out_of_stock": 0,
            "low_stock": 0,
            "critical": 0,
            "safety_stock": 0,
            "total_value": 0
        }

# Health check endpoint
@router.get("/health/check")
async def spares_health_check():
    """Health check endpoint for spares router"""
    try:
        response = supabase.table("spares").select("id", count="exact").limit(1).execute()
        
        return {
            "status": "healthy", 
            "service": "spares", 
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat(),
            "table_exists": True
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

# Test endpoint
@router.get("/test/connection")
async def test_connection():
    """Test database connection"""
    try:
        response = supabase.table("spares").select("id", count="exact").limit(1).execute()
        
        return {
            "status": "ok",
            "database": "connected",
            "table": "spares",
            "record_count": len(response.data) if response.data else 0
        }
    except Exception as e:
        return {"status": "error", "database": "disconnected", "error": str(e)}

# Export data endpoint
@router.get("/export/data")
async def export_spares():
    """Export all spares as JSON"""
    try:
        response = supabase.table("spares").select("*").order("stock_code").execute()

        spares = response.data or []
        for record in spares:
            convert_dates_to_iso(record)

        return {
            "export_date": datetime.now().isoformat(),
            "count": len(spares),
            "spares": spares
        }

    except Exception as e:
        logger.error(f"Error exporting spares: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error exporting spares: {str(e)}")

# ── Saved Spare Requisitions ──────────────────────────────────────────────────
# Run this SQL in Supabase before using these endpoints:
#
#   CREATE TABLE spare_requisitions (
#     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
#     name TEXT NOT NULL,
#     saved_at TIMESTAMPTZ DEFAULT NOW(),
#     updated_at TIMESTAMPTZ DEFAULT NOW(),
#     requester TEXT,
#     reason TEXT,
#     urgency TEXT DEFAULT 'routine',
#     priority TEXT DEFAULT 'medium',
#     required_for TEXT,
#     lines JSONB DEFAULT '[]',
#     grand_total NUMERIC DEFAULT 0
#   );

class SavedSpareReqCreate(BaseModel):
    name: str = Field(..., min_length=1)
    requester: Optional[str] = None
    reason: Optional[str] = None
    urgency: str = 'routine'
    priority: str = 'medium'
    required_for: Optional[str] = None
    lines: List[dict] = Field(default_factory=list)
    grand_total: float = 0.0

@router.get("/saved-requisitions")
async def get_saved_spare_requisitions():
    """Fetch all saved spare requisitions from Supabase"""
    try:
        response = supabase.table("spare_requisitions").select("*").order("updated_at", desc=True).execute()
        return response.data or []
    except Exception as e:
        logger.warning(f"spare_requisitions table may not exist yet: {e}")
        return []

@router.post("/saved-requisitions", status_code=201)
async def create_saved_spare_requisition(data: SavedSpareReqCreate):
    """Save a spare requisition to Supabase"""
    try:
        now = datetime.utcnow().isoformat()
        row = {**data.dict(), "saved_at": now, "updated_at": now}
        response = supabase.table("spare_requisitions").insert(row).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to save requisition")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/saved-requisitions/{req_id}")
async def update_saved_spare_requisition(req_id: str, data: SavedSpareReqCreate):
    """Update a saved spare requisition"""
    try:
        now = datetime.utcnow().isoformat()
        row = {**data.dict(), "updated_at": now}
        response = supabase.table("spare_requisitions").update(row).eq("id", req_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Saved requisition not found")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/saved-requisitions/{req_id}")
async def delete_saved_spare_requisition(req_id: str):
    """Delete a saved spare requisition"""
    try:
        supabase.table("spare_requisitions").delete().eq("id", req_id).execute()
        return {"message": "Deleted", "id": req_id}
    except Exception as e:
        logger.error(f"Error deleting spare requisition: {e}")
        raise HTTPException(status_code=500, detail=str(e))