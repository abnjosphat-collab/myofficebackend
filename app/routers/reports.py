# app/routers/reports.py
from fastapi import APIRouter, HTTPException, Query, Depends
from app.auth import get_current_user, require_role
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import json
import csv
import io
import uuid
import pandas as pd
import numpy as np
from io import BytesIO

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Pydantic models for request/response
class ReportCreate(BaseModel):
    type: str
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    format: str = "json"

class CustomReportCreate(BaseModel):
    filters: Dict[str, Any]
    columns: List[str]
    format: str = "json"

class ReportResponse(BaseModel):
    id: str
    title: str
    type: str
    status: str
    generatedAt: str
    period: str
    size: str
    downloadUrl: str

# Mock data - In production, this would come from your database
def get_mock_reports():
    return [
        {
            'id': '1',
            'title': 'Monthly Overtime Summary',
            'type': 'overtime',
            'status': 'generated',
            'generatedAt': '2024-01-15T10:30:00Z',
            'period': 'January 2024',
            'size': '2.4 MB',
            'downloadUrl': '/api/reports/1/download'
        },
        {
            'id': '2',
            'title': 'Asset Utilization Report',
            'type': 'assets',
            'status': 'generated',
            'generatedAt': '2024-01-14T15:45:00Z',
            'period': 'Q4 2023',
            'size': '1.8 MB',
            'downloadUrl': '/api/reports/2/download'
        },
        {
            'id': '3',
            'title': 'Personnel Performance Review',
            'type': 'personnel',
            'status': 'pending',
            'generatedAt': '2024-01-15T09:15:00Z',
            'period': 'December 2023',
            'size': '3.1 MB',
            'downloadUrl': '/api/reports/3/download'
        },
        {
            'id': '4',
            'title': 'Safety Compliance Audit',
            'type': 'safety',
            'status': 'generated',
            'generatedAt': '2024-01-13T14:20:00Z',
            'period': 'January 2024',
            'size': '4.2 MB',
            'downloadUrl': '/api/reports/4/download'
        },
        {
            'id': '5',
            'title': 'Maintenance Schedule Analysis',
            'type': 'maintenance',
            'status': 'failed',
            'generatedAt': '2024-01-12T11:00:00Z',
            'period': 'Q4 2023',
            'size': '2.1 MB',
            'downloadUrl': '/api/reports/5/download'
        }
    ]

def get_analytics_summary():
    """Generate analytics summary data"""
    return {
        'totalOvertimeHours': 245,
        'activeEmployees': 125,
        'operationalAssets': 67,
        'safetyIncidents': 1,
        'pendingRequests': 15,
        'completionRate': 86
    }

def generate_overtime_report(start_date, end_date, format='json'):
    """Generate overtime report data"""
    # Mock overtime data - in production, query your database
    overtime_data = [
        {
            'employeeId': 'EMP001',
            'employeeName': 'John Smith',
            'department': 'Engineering',
            'date': '2024-01-15',
            'hours': 4.5,
            'reason': 'Project Deadline',
            'status': 'approved',
            'approvedBy': 'Jane Doe'
        },
        {
            'employeeId': 'EMP002',
            'employeeName': 'Sarah Johnson',
            'department': 'Operations',
            'date': '2024-01-14',
            'hours': 3.0,
            'reason': 'Emergency Maintenance',
            'status': 'approved',
            'approvedBy': 'Mike Wilson'
        },
        {
            'employeeId': 'EMP003',
            'employeeName': 'Robert Brown',
            'department': 'Safety',
            'date': '2024-01-13',
            'hours': 2.0,
            'reason': 'Safety Audit',
            'status': 'pending',
            'approvedBy': ''
        }
    ]
    
    summary = {
        'totalHours': sum(item['hours'] for item in overtime_data),
        'approvedHours': sum(item['hours'] for item in overtime_data if item['status'] == 'approved'),
        'pendingHours': sum(item['hours'] for item in overtime_data if item['status'] == 'pending'),
        'employeeCount': len(set(item['employeeId'] for item in overtime_data)),
        'departmentBreakdown': {
            dept: sum(item['hours'] for item in overtime_data if item['department'] == dept)
            for dept in set(item['department'] for item in overtime_data)
        }
    }
    
    if format == 'csv':
        return convert_to_csv(overtime_data)
    elif format == 'pdf':
        return generate_pdf_report('overtime', overtime_data, summary)
    else:
        return {
            'summary': summary,
            'data': overtime_data,
            'generatedAt': datetime.utcnow().isoformat() + 'Z',
            'period': f"{start_date} to {end_date}"
        }

def generate_personnel_report(format='json'):
    """Generate personnel report data"""
    personnel_data = [
        {
            'employeeId': 'EMP001',
            'name': 'John Smith',
            'department': 'Engineering',
            'position': 'Senior Engineer',
            'hireDate': '2020-03-15',
            'status': 'active',
            'email': 'john.smith@company.com',
            'phone': '+1234567890'
        },
        {
            'employeeId': 'EMP002',
            'name': 'Sarah Johnson',
            'department': 'Operations',
            'position': 'Operations Manager',
            'hireDate': '2019-07-22',
            'status': 'active',
            'email': 'sarah.johnson@company.com',
            'phone': '+1234567891'
        },
        {
            'employeeId': 'EMP003',
            'name': 'Robert Brown',
            'department': 'Safety',
            'position': 'Safety Officer',
            'hireDate': '2021-01-10',
            'status': 'active',
            'email': 'robert.brown@company.com',
            'phone': '+1234567892'
        }
    ]
    
    summary = {
        'totalEmployees': len(personnel_data),
        'departments': {
            dept: len([emp for emp in personnel_data if emp['department'] == dept])
            for dept in set(emp['department'] for emp in personnel_data)
        },
        'activeEmployees': len([emp for emp in personnel_data if emp['status'] == 'active']),
        'averageTenure': '2.5 years'  # This would be calculated
    }
    
    if format == 'csv':
        return convert_to_csv(personnel_data)
    elif format == 'pdf':
        return generate_pdf_report('personnel', personnel_data, summary)
    else:
        return {
            'summary': summary,
            'data': personnel_data,
            'generatedAt': datetime.utcnow().isoformat() + 'Z'
        }

def generate_assets_report(format='json'):
    """Generate assets report data"""
    assets_data = [
        {
            'assetId': 'AST001',
            'name': 'Excavator X-100',
            'category': 'Heavy Equipment',
            'status': 'operational',
            'location': 'Site A',
            'lastMaintenance': '2024-01-10',
            'nextMaintenance': '2024-02-10',
            'utilization': 85
        },
        {
            'assetId': 'AST002',
            'name': 'Drill Rig D-200',
            'category': 'Drilling Equipment',
            'status': 'maintenance',
            'location': 'Site B',
            'lastMaintenance': '2024-01-08',
            'nextMaintenance': '2024-01-25',
            'utilization': 72
        },
        {
            'assetId': 'AST003',
            'name': 'Haul Truck H-300',
            'category': 'Transportation',
            'status': 'operational',
            'location': 'Site A',
            'lastMaintenance': '2024-01-12',
            'nextMaintenance': '2024-02-12',
            'utilization': 91
        }
    ]
    
    summary = {
        'totalAssets': len(assets_data),
        'operationalAssets': len([asset for asset in assets_data if asset['status'] == 'operational']),
        'underMaintenance': len([asset for asset in assets_data if asset['status'] == 'maintenance']),
        'averageUtilization': sum(asset['utilization'] for asset in assets_data) / len(assets_data),
        'categoryBreakdown': {
            category: len([asset for asset in assets_data if asset['category'] == category])
            for category in set(asset['category'] for asset in assets_data)
        }
    }
    
    if format == 'csv':
        return convert_to_csv(assets_data)
    elif format == 'pdf':
        return generate_pdf_report('assets', assets_data, summary)
    else:
        return {
            'summary': summary,
            'data': assets_data,
            'generatedAt': datetime.utcnow().isoformat() + 'Z'
        }

def convert_to_csv(data):
    """Convert data to CSV format"""
    if not data:
        return ''
    
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    
    return output.getvalue()

def generate_pdf_report(report_type, data, summary):
    """Generate PDF report - placeholder for actual PDF generation"""
    # In production, use a library like ReportLab, WeasyPrint, or pdfkit
    # This is a simplified placeholder
    pdf_content = f"""
    {report_type.upper()} REPORT
    Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}
    
    SUMMARY:
    {json.dumps(summary, indent=2)}
    
    DATA:
    {json.dumps(data, indent=2)}
    """
    
    return pdf_content

# Routes
@router.get("/", response_model=List[ReportResponse])
async def get_reports():
    """Get all reports"""
    try:
        reports = get_mock_reports()
        return reports
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{report_id}", response_model=ReportResponse)
async def get_report(report_id: str):
    """Get specific report"""
    try:
        reports = get_mock_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
            
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{report_id}/download")
async def download_report(
    report_id: str,
    format: str = Query("json", description="Download format: json, csv, pdf")
):
    """Download report in specified format"""
    try:
        reports = get_mock_reports()
        report = next((r for r in reports if r['id'] == report_id), None)
        
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        
        # Generate report data based on type
        if report['type'] == 'overtime':
            report_data = generate_overtime_report(
                start_date='2024-01-01', 
                end_date='2024-01-15', 
                format=format
            )
        elif report['type'] == 'personnel':
            report_data = generate_personnel_report(format=format)
        elif report['type'] == 'assets':
            report_data = generate_assets_report(format=format)
        else:
            raise HTTPException(status_code=400, detail="Report type not supported")
        
        if format == 'csv':
            return {
                "csv": report_data,
                "filename": f"{report['title'].replace(' ', '_')}.csv"
            }
        elif format == 'pdf':
            # In production, return actual PDF file
            return {
                "message": "PDF generation would happen here", 
                "data": report_data
            }
        else:
            return report_data
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/analytics/summary")
async def get_analytics_summary_route():
    """Get analytics summary"""
    try:
        summary = get_analytics_summary()
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/generate")
async def generate_report(report_data: ReportCreate, current_user: dict = Depends(get_current_user)):
    """Generate a new report"""
    try:
        report_type = report_data.type
        start_date = report_data.startDate
        end_date = report_data.endDate
        format = report_data.format
        
        if not report_type:
            raise HTTPException(status_code=400, detail="Report type is required")
        
        # Generate report based on type
        if report_type == 'overtime':
            report_data_result = generate_overtime_report(start_date, end_date, format)
        elif report_type == 'personnel':
            report_data_result = generate_personnel_report(format)
        elif report_type == 'assets':
            report_data_result = generate_assets_report(format)
        elif report_type == 'safety':
            # Placeholder for safety reports
            report_data_result = {'message': 'Safety report generation would be implemented here'}
        elif report_type == 'maintenance':
            # Placeholder for maintenance reports
            report_data_result = {'message': 'Maintenance report generation would be implemented here'}
        else:
            raise HTTPException(status_code=400, detail="Invalid report type")
        
        # Create new report entry
        new_report = {
            'id': str(uuid.uuid4()),
            'title': f'{report_type.title()} Report - {datetime.utcnow().strftime("%Y-%m-%d")}',
            'type': report_type,
            'status': 'generated',
            'generatedAt': datetime.utcnow().isoformat() + 'Z',
            'period': f"{start_date} to {end_date}" if start_date and end_date else 'Custom Period',
            'size': '1.5 MB',  # This would be calculated
            'downloadUrl': f'/api/reports/{len(get_mock_reports()) + 1}/download'
        }
        
        return {
            'report': new_report,
            'data': report_data_result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{report_id}")
async def delete_report(report_id: str, current_user: dict = Depends(require_role('manager'))):
    """Delete a report"""
    try:
        # In production, this would delete from database
        # For now, just return success
        return {"message": "Report deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stats/summary")
async def get_reports_stats():
    """Get reports statistics"""
    try:
        reports = get_mock_reports()
        
        stats = {
            'totalReports': len(reports),
            'reportsByType': {},
            'reportsByStatus': {},
            'recentActivity': len([r for r in reports if datetime.fromisoformat(r['generatedAt'].replace('Z', '')) > datetime.utcnow() - timedelta(days=7)])
        }
        
        # Count by type
        for report in reports:
            stats['reportsByType'][report['type']] = stats['reportsByType'].get(report['type'], 0) + 1
            stats['reportsByStatus'][report['status']] = stats['reportsByStatus'].get(report['status'], 0) + 1
        
        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Custom report generation with filters
@router.post("/custom")
async def generate_custom_report(custom_report: CustomReportCreate, current_user: dict = Depends(get_current_user)):
    """Generate custom report with advanced filters"""
    try:
        filters = custom_report.filters
        columns = custom_report.columns
        format = custom_report.format
        
        # This would query your database based on filters
        # For now, return mock data based on filter type
        if filters.get('type') == 'overtime':
            report_data = generate_overtime_report(
                filters.get('startDate'),
                filters.get('endDate'),
                format
            )
        else:
            report_data = {'message': 'Custom report data would be generated here based on filters'}
        
        return {
            'filters': filters,
            'columns': columns,
            'data': report_data,
            'generatedAt': datetime.utcnow().isoformat() + 'Z'
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))