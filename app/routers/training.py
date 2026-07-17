# app/routers/training.py - FastAPI Router for Compliance Management

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from app.auth import get_current_user, require_role
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import date, timedelta
import uuid
import random
import time

# --- Configuration & Router Setup ---
router = APIRouter(
    prefix="/api/training",
    tags=["Training & Certification"],
)

# --- Pydantic Schemas & Utilities ---

def check_status(expiry_date: date) -> str:
    """Calculates the compliance status based on the expiry date."""
    today = date.today()
    if expiry_date < today:
        return 'Expired'
    if expiry_date <= today + timedelta(days=90):
        return 'Due Soon'
    return 'Valid'

class CertificateRecord(BaseModel):
    """Schema for a single training and certification record."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    employee_id: str
    employee_name: str
    department: str
    certification_name: str
    expiry_date: date
    required_refresher: str
    certificate_url: Optional[str] = None 
    status: str = Field(default="Valid")  # Set default value to avoid validation errors
    
    # Ensures status is set correctly when model is initialized
    def update_status(self):
        self.status = check_status(self.expiry_date)
        return self

# --- In-Memory Database Simulation ---
# Initialize the database with mock data
CERTIFICATIONS_DB: List[CertificateRecord] = [
    CertificateRecord(
        employee_id='E001', 
        employee_name='John Doe', 
        certification_name='First Aid & CPR', 
        expiry_date=date(2025, 1, 15), 
        required_refresher='BLS Refresher', 
        department='Safety', 
        certificate_url='/docs/cert_E001_FA.pdf',
        status='Valid'  # Explicitly set status
    ).update_status(),
    CertificateRecord(
        employee_id='E002', 
        employee_name='Jane Smith', 
        certification_name='Heavy Equipment Operation', 
        expiry_date=date(2026, 6, 20), 
        required_refresher='Annual HEO Check', 
        department='Mining',
        certificate_url='/docs/cert_E002_HEO.pdf',
        status='Valid'
    ).update_status(),
    CertificateRecord(
        employee_id='E003', 
        employee_name='Mike Johnson', 
        certification_name='Methane Gas Monitoring', 
        expiry_date=date(2025, 12, 1), 
        required_refresher='Gas Monitor Refresher', 
        department='Geology',
        certificate_url='/docs/cert_E003_MGM.pdf',
        status='Valid'
    ).update_status(),
    CertificateRecord(
        employee_id='E004', 
        employee_name='Sarah Lee', 
        certification_name='Blasting Permit (Surface)', 
        expiry_date=date(2026, 3, 10), 
        required_refresher='Bi-Annual Recert', 
        department='Mining',
        certificate_url='/docs/cert_E004_BP.pdf',
        status='Valid'
    ).update_status(),
    CertificateRecord(
        employee_id='E005', 
        employee_name='Tom Wilson', 
        certification_name='First Aid & CPR', 
        expiry_date=date(2025, 11, 20), 
        required_refresher='BLS Refresher', 
        department='Safety',
        certificate_url='/docs/cert_E005_FA.pdf',
        status='Valid'
    ).update_status(),
]

def find_record(record_id: str) -> Optional[CertificateRecord]:
    """Finds a record by its unique ID."""
    for record in CERTIFICATIONS_DB:
        if record.id == record_id:
            return record
    return None

# --- API Endpoints ---

@router.get("/", response_model=List[CertificateRecord])
async def get_all_certifications():
    """Retrieves all certification records."""
    # Ensure statuses are calculated before sending
    for record in CERTIFICATIONS_DB:
        record.update_status()
    # Simulate network delay for better user experience on the frontend during loading
    await asyncio.sleep(0.3) 
    return CERTIFICATIONS_DB

@router.post("/", response_model=CertificateRecord)
async def create_new_certification(
    employee_id: str = Form(...),
    employee_name: str = Form(...),
    department: str = Form(...),
    certification_name: str = Form(...),
    expiry_date: date = Form(..., description="Format: YYYY-MM-DD"),
    required_refresher: str = Form(...),
    certificate_file: Optional[UploadFile] = File(None)
, current_user: dict = Depends(get_current_user)):
    """Creates a new certification record and handles the file upload."""
    
    file_url = None
    if certificate_file:
        # SIMULATION: Generate a mock URL.
        # REAL WORLD: Save the file to S3/GCS/Supabase Storage and retrieve the permanent URL here.
        file_extension = certificate_file.filename.split('.')[-1] if '.' in certificate_file.filename else 'pdf'
        file_url = f"/storage/certs/{uuid.uuid4()}.{file_extension}"
        
        # Simulate file processing time
        await asyncio.sleep(0.5)

    # Create the certificate record with all required fields including status
    new_record = CertificateRecord(
        employee_id=employee_id,
        employee_name=employee_name,
        department=department,
        certification_name=certification_name,
        expiry_date=expiry_date,
        required_refresher=required_refresher,
        certificate_url=file_url,
        status=check_status(expiry_date)  # Calculate status immediately
    )
    
    CERTIFICATIONS_DB.append(new_record)
    
    return new_record

@router.get("/{record_id}", response_model=CertificateRecord)
async def get_certification(record_id: str):
    """Get a specific certification record by ID."""
    record = find_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Certification record not found")
    
    # Update status before returning
    record.update_status()
    return record

@router.put("/{record_id}", response_model=CertificateRecord)
async def update_certification(
    record_id: str,
    employee_id: str = Form(None),
    employee_name: str = Form(None),
    department: str = Form(None),
    certification_name: str = Form(None),
    expiry_date: date = Form(None),
    required_refresher: str = Form(None),
    certificate_file: Optional[UploadFile] = File(None)
, current_user: dict = Depends(get_current_user)):
    """Update an existing certification record."""
    record = find_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Certification record not found")
    
    # Update fields if provided
    if employee_id is not None:
        record.employee_id = employee_id
    if employee_name is not None:
        record.employee_name = employee_name
    if department is not None:
        record.department = department
    if certification_name is not None:
        record.certification_name = certification_name
    if expiry_date is not None:
        record.expiry_date = expiry_date
    if required_refresher is not None:
        record.required_refresher = required_refresher
    
    # Handle file upload if provided
    if certificate_file:
        file_extension = certificate_file.filename.split('.')[-1] if '.' in certificate_file.filename else 'pdf'
        record.certificate_url = f"/storage/certs/{uuid.uuid4()}.{file_extension}"
        await asyncio.sleep(0.5)
    
    # Update status based on new expiry date
    record.update_status()
    
    return record

@router.delete("/{record_id}")
async def delete_certification(record_id: str, current_user: dict = Depends(require_role('manager'))):
    """Deletes a certification record."""
    global CERTIFICATIONS_DB
    
    initial_length = len(CERTIFICATIONS_DB)
    # Filter out the record to be deleted
    CERTIFICATIONS_DB = [rec for rec in CERTIFICATIONS_DB if rec.id != record_id]
    
    if len(CERTIFICATIONS_DB) == initial_length:
        raise HTTPException(status_code=404, detail="Record not found")
    
    return {"message": "Certification record deleted successfully"}

# --- Compliance Reporting Features ---

@router.get("/reports/compliance_rate")
async def get_compliance_rate():
    """Calculates and returns the overall compliance percentage."""
    total = len(CERTIFICATIONS_DB)
    
    if total == 0:
        return {"compliance_rate": 100.0, "total_tracked": 0, "non_compliant": 0}
        
    expired_count = sum(1 for rec in CERTIFICATIONS_DB if check_status(rec.expiry_date) == 'Expired')
    
    compliance_rate = round(((total - expired_count) / total) * 100, 2)
    
    return {
        "compliance_rate": compliance_rate,
        "total_tracked": total,
        "non_compliant": expired_count
    }

@router.get("/reports/due_refreshers")
async def get_due_refreshers():
    """Returns a list of required refresher courses and the count of employees needing them."""
    refresher_counts: Dict[str, int] = {}
    
    for rec in CERTIFICATIONS_DB:
        # Count non-expired records that require a refresher
        if check_status(rec.expiry_date) != 'Expired' and rec.required_refresher not in ('N/A', '', None):
            refresher = rec.required_refresher
            refresher_counts[refresher] = refresher_counts.get(refresher, 0) + 1
            
    result = [{"refresher": k, "employees_due": v} for k, v in refresher_counts.items()]
    
    # Return the top 3 most-needed refreshers
    return sorted(result, key=lambda x: x['employees_due'], reverse=True)[:3]

@router.get("/employee/{employee_id}", response_model=List[CertificateRecord])
async def get_employee_certifications(employee_id: str):
    """Get all certifications for a specific employee."""
    employee_certs = [rec for rec in CERTIFICATIONS_DB if rec.employee_id == employee_id]
    
    # Update status for all records
    for cert in employee_certs:
        cert.update_status()
    
    return employee_certs

@router.get("/alerts/expiring")
async def get_expiring_certifications(days: int = 90):
    """Get certifications expiring within the specified number of days."""
    today = date.today()
    expiring_certs = []
    
    for cert in CERTIFICATIONS_DB:
        days_until_expiry = (cert.expiry_date - today).days
        if 0 <= days_until_expiry <= days:
            cert_data = cert.dict()
            cert_data["days_until_expiry"] = days_until_expiry
            expiring_certs.append(cert_data)
    
    # Sort by days until expiry (ascending)
    expiring_certs.sort(key=lambda x: x["days_until_expiry"])
    
    return {
        "days_threshold": days,
        "count": len(expiring_certs),
        "certifications": expiring_certs
    }

@router.get("/stats/summary")
async def get_training_stats():
    """Get training and certification statistics."""
    total_certifications = len(CERTIFICATIONS_DB)
    
    status_counts = {}
    department_counts = {}
    
    for cert in CERTIFICATIONS_DB:
        # Update status first
        cert.update_status()
        
        # Count by status
        status = cert.status
        status_counts[status] = status_counts.get(status, 0) + 1
        
        # Count by department
        department = cert.department
        department_counts[department] = department_counts.get(department, 0) + 1
    
    return {
        "totalCertifications": total_certifications,
        "statusDistribution": status_counts,
        "departmentDistribution": department_counts,
        "complianceRate": round(((total_certifications - status_counts.get('Expired', 0)) / total_certifications) * 100, 2) if total_certifications > 0 else 100.0
    }

# Import asyncio for proper async sleep
import asyncio