# backend/app/routers/equipment.py
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, computed_field
from typing import List, Optional
from datetime import date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.cache import cached, invalidate_namespace
from app.db_helpers import get_or_404
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

class EquipmentBase(BaseModel):
    """The portable "what is this asset" contract — every field but `name` is
    optional since the register isn't fully populated yet, and every field here
    is safe to hand to another module's equipment picker/autofill (compressors,
    breakdowns, etc.) without dragging in register-only bookkeeping."""
    # Primary fields
    id: Optional[int] = None
    equipment_id: Optional[str] = Field(None, description="Unique equipment identifier")
    name: str = Field(..., min_length=1, description="Equipment name")
    description: Optional[str] = None
    serial_number: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None

    # Status and location
    # Default must satisfy the equipment_status_check DB constraint, which only
    # accepts the form's values: operational | maintenance | out_of_service |
    # reserved. The old default "Available" violated it — any create that omitted
    # status got a 500.
    status: Optional[str] = "operational"
    location: Optional[str] = None
    department: Optional[str] = None
    assigned_to: Optional[str] = None

    # Purchase information
    purchase_date: Optional[date] = None
    purchase_price: Optional[float] = None
    purchase_cost: Optional[float] = None
    supplier: Optional[str] = None
    supplier_contact: Optional[str] = None
    supplier_phone: Optional[str] = None
    warranty_info: Optional[str] = None
    warranty_expiry: Optional[date] = None

    # Additional info
    model: Optional[str] = None
    manufacturer: Optional[str] = Field(None, description="Who made it — distinct from `supplier`, who it was bought from")
    power_rating: Optional[str] = Field(None, description="Structured capacity, e.g. \"45kW\" — unit varies (kW/HP), so a formatted string, not a bare float")
    criticality: Optional[str] = Field(None, description="High / Medium / Low")
    commission_date: Optional[date] = None
    specifications: Optional[str] = None
    maintenance_interval: Optional[int] = None
    maintenance_notes: Optional[str] = None
    condition: Optional[str] = None
    barcode: Optional[str] = None
    qr_code: Optional[str] = None
    image_url: Optional[str] = None
    notes: Optional[str] = None

    @computed_field
    @property
    def display_name(self) -> str:
        """One consistent "how do I show this equipment as one string" — every
        picker/dropdown/breadcrumb should call this instead of re-deriving its
        own format. Excluded from the Supabase write payload (see the two
        `.dict(..., exclude={'display_name'})` call sites below) since it isn't
        a real column."""
        return f"{self.equipment_id} — {self.name}" if self.equipment_id else self.name

    def matches(self, query: str) -> bool:
        """The one search-match rule, so every equipment search/filter (this
        router, EquipmentAutocomplete, a future compressor picker) agrees on
        what "matches" means instead of each re-implementing it slightly
        differently."""
        q = query.lower()
        return any(q in (v or '').lower() for v in (self.name, self.equipment_id, self.model, self.manufacturer))


class Equipment(EquipmentBase):
    """The full equipment-register record — every existing endpoint uses this.
    Kept as a distinct type from EquipmentBase (currently field-identical) so a
    future register-only field has somewhere to go without pulling other
    modules' equipment pickers along for the ride. last_maintenance/
    next_maintenance/current_value/depreciation_rate USED to live here —
    removed: maintenance dates belong to the maintenance module, valuation to
    accounting, not the equipment register."""
    class Config:
        json_encoders = {date: lambda v: v.isoformat()}

def process_dates_for_db(data: dict) -> dict:
    """Convert date objects to ISO strings for Supabase"""
    processed_data = data.copy()

    date_fields = ['purchase_date', 'warranty_expiry', 'commission_date']
    for field in date_fields:
        if isinstance(processed_data.get(field), date):
            processed_data[field] = processed_data[field].isoformat()

    return processed_data

def process_dates_from_db(data: dict) -> dict:
    """Convert ISO date strings back to date objects"""
    processed_data = data.copy()
    
    date_fields = ['purchase_date', 'warranty_expiry', 'commission_date']
    for field in date_fields:
        if processed_data.get(field):
            try:
                if isinstance(processed_data[field], str):
                    processed_data[field] = date.fromisoformat(processed_data[field])
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing {field}: {e}")
                processed_data[field] = None
    
    # Ensure tags is always a list
    if processed_data.get('tags') is None:
        processed_data['tags'] = []
    
    return processed_data

def get_supabase_data(response):
    """Helper to extract data from Supabase response"""
    if hasattr(response, 'data'):
        return response.data
    return response

def generate_equipment_id():
    """Generate a unique equipment ID"""
    import time
    timestamp = int(time.time() * 1000)  # milliseconds
    return f"EQ-{timestamp}"

# --- Routes ---

@router.get("")
@router.get("/")
@cached("equipment", ttl=60)
async def get_equipment():
    """Retrieve all equipment from the database."""
    try:
        response = supabase.table("equipment").select("*").execute()
        data = get_supabase_data(response)
        
        if not data:
            return []
        
        processed_equipment = [process_dates_from_db(item) for item in data]
        return processed_equipment
    except Exception as e:
        logger.error(f"Error fetching equipment: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching equipment: {str(e)}")

@router.get("/{equipment_id}", dependencies=[Depends(get_current_user)])
async def get_equipment_item(equipment_id: int):
    """Retrieve a specific equipment item by ID."""
    try:
        row = get_or_404(supabase, "equipment", equipment_id, detail=f"Equipment with ID {equipment_id} not found")
        equipment_data = process_dates_from_db(row)
        return equipment_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching equipment {equipment_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching equipment: {str(e)}")

@router.post("")
@router.post("/")
async def create_equipment(equipment: Equipment, current_user: dict = Depends(get_current_user)):
    """Create a new equipment record."""
    try:
        # display_name is a computed field (derived, not a real column) — exclude
        # it from the write payload or Supabase rejects the insert.
        data_to_insert = equipment.dict(exclude_none=True, exclude={'display_name'})
        
        # Auto-generate equipment_id if not provided
        if not data_to_insert.get('equipment_id'):
            data_to_insert['equipment_id'] = generate_equipment_id()
        
        data_to_insert = process_dates_for_db(data_to_insert)
        
        logger.debug(f"Inserting equipment data: {data_to_insert}")
        
        result = supabase.table("equipment").insert(data_to_insert).execute()
        created_data = get_supabase_data(result)
            
        if not created_data:
            raise HTTPException(status_code=500, detail="No data returned after insertion")
            
        created_equipment = process_dates_from_db(created_data[0])
        await invalidate_namespace("equipment")
        return created_equipment
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating equipment: {str(e)}")
        # Return more detailed error info
        error_detail = str(e)
        if hasattr(e, 'message'):
            error_detail = e.message
        raise HTTPException(status_code=500, detail=f"Error creating equipment: {error_detail}")

@router.put("/{equipment_id}")
async def update_equipment(equipment_id: int, updated: Equipment, current_user: dict = Depends(get_current_user)):
    """Update an existing equipment record."""
    try:
        existing_response = supabase.table("equipment").select("id").eq("id", equipment_id).execute()
        existing_data = get_supabase_data(existing_response)
            
        if not existing_data:
            raise HTTPException(
                status_code=404, 
                detail=f"Equipment with ID {equipment_id} not found"
            )
        
        data_to_update = updated.dict(exclude_none=True, exclude={'display_name'})
        data_to_update = process_dates_for_db(data_to_update)
        
        # Don't update equipment_id if it already exists
        if 'equipment_id' in data_to_update and not data_to_update['equipment_id']:
            data_to_update.pop('equipment_id')
        
        result = supabase.table("equipment").update(data_to_update).eq("id", equipment_id).execute()
        updated_data = get_supabase_data(result)
            
        if not updated_data:
            raise HTTPException(status_code=500, detail="No data returned after update")
            
        updated_equipment = process_dates_from_db(updated_data[0])
        await invalidate_namespace("equipment")
        return updated_equipment
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating equipment {equipment_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating equipment: {str(e)}")

@router.delete("/{equipment_id}")
async def delete_equipment(equipment_id: int, current_user: dict = Depends(require_role('manager'))):
    """Delete an equipment record."""
    try:
        existing_response = supabase.table("equipment").select("id, name").eq("id", equipment_id).execute()
        existing_data = get_supabase_data(existing_response)
            
        if not existing_data:
            raise HTTPException(
                status_code=404, 
                detail=f"Equipment with ID {equipment_id} not found"
            )
        
        equipment_name = existing_data[0].get('name', 'Unknown')
        
        supabase.table("equipment").delete().eq("id", equipment_id).execute()
        await invalidate_namespace("equipment")

        return {
            "success": True,
            "detail": f"Equipment {equipment_id} ({equipment_name}) successfully deleted",
            "deleted_id": equipment_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting equipment {equipment_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting equipment: {str(e)}")

@router.get("/health/status", tags=["Health"])
async def equipment_health():
    """Check if the equipment service is operational"""
    try:
        response = supabase.table("equipment").select("id").limit(1).execute()
        data = get_supabase_data(response)
        
        return {
            "status": "healthy",
            "service": "equipment",
            "database": "connected",
            "message": "Equipment service is operational"
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Equipment service is unhealthy: {str(e)}"
        )