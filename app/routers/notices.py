# app/api/notices.py
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, date
from app.supabase_client import supabase
from app.auth import get_current_user, require_role

router = APIRouter()

# Pydantic Model - matches SQL exactly
class NoticeCreate(BaseModel):
    title: str
    content: str
    date: date
    category: str = "General"
    priority: str = "Medium"
    status: str = "Draft"
    is_pinned: bool = False
    requires_acknowledgment: bool = False
    author: Optional[str] = None
    department: Optional[str] = "General"
    expires_at: Optional[date] = None
    target_audience: Optional[str] = "All Employees"
    notification_type: Optional[str] = "General Announcement"
    attachment_name: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_size: Optional[str] = None

class NoticeUpdate(NoticeCreate):
    pass

# GET all notices
@router.get("", dependencies=[Depends(get_current_user)])
async def get_notices(
    category: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    is_pinned: Optional[bool] = Query(None),
    search: Optional[str] = Query(None)
):
    try:
        query = supabase.table("notices").select("*")
        
        if category and category != 'all':
            query = query.eq("category", category)
        if priority and priority != 'all':
            query = query.eq("priority", priority)
        if status and status != 'all':
            query = query.eq("status", status)
        if department and department != 'all':
            query = query.eq("department", department)
        if is_pinned is not None:
            query = query.eq("is_pinned", is_pinned)
        if search:
            query = query.or_(f"title.ilike.%{search}%,content.ilike.%{search}%")
            
        response = query.order("date", desc=True).execute()
        return response.data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# POST create notice
@router.post("")
async def create_notice(notice: NoticeCreate, current_user: dict = Depends(get_current_user)):
    try:
        data = notice.dict()
        
        # Convert dates to ISO strings
        if data.get('date'):
            data['date'] = data['date'].isoformat()
        if data.get('expires_at'):
            data['expires_at'] = data['expires_at'].isoformat()
        
        response = supabase.table("notices").insert(data).execute()
        
        if response.data:
            return response.data[0]
        raise HTTPException(status_code=500, detail="Failed to create notice")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# GET single notice
@router.get("/{notice_id}", dependencies=[Depends(get_current_user)])
async def get_notice(notice_id: str):
    try:
        response = supabase.table("notices").select("*").eq("id", notice_id).execute()
        
        if not response.data:
            raise HTTPException(status_code=404, detail="Notice not found")
        
        return response.data[0]
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# PUT update notice
@router.put("/{notice_id}")
async def update_notice(notice_id: str, notice: NoticeUpdate, current_user: dict = Depends(get_current_user)):
    try:
        # Check if exists
        check = supabase.table("notices").select("id").eq("id", notice_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Notice not found")
        
        data = notice.dict()
        
        # Convert dates to ISO strings
        if data.get('date'):
            data['date'] = data['date'].isoformat()
        if data.get('expires_at'):
            data['expires_at'] = data['expires_at'].isoformat()
        
        response = supabase.table("notices").update(data).eq("id", notice_id).execute()
        
        if response.data:
            return response.data[0]
        raise HTTPException(status_code=500, detail="Failed to update notice")
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# DELETE notice
@router.delete("/{notice_id}")
async def delete_notice(notice_id: str, current_user: dict = Depends(require_role('manager'))):
    try:
        # Check if exists
        check = supabase.table("notices").select("id").eq("id", notice_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Notice not found")
        
        supabase.table("notices").delete().eq("id", notice_id).execute()
        return {"success": True, "message": "Notice deleted"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# GET statistics
@router.get("/stats/summary", dependencies=[Depends(get_current_user)])
async def get_stats():
    try:
        # Get counts
        response = supabase.table("notices").select("*").execute()
        notices = response.data
        
        stats = {
            "total_notices": len(notices),
            "status_breakdown": {},
            "priority_breakdown": {},
            "category_breakdown": {},
            "pinned_count": 0,
            "expired_count": 0,
            "expiring_soon_count": 0
        }
        
        for notice in notices:
            # Count statuses
            status = notice.get('status', 'Draft')
            stats["status_breakdown"][status] = stats["status_breakdown"].get(status, 0) + 1
            
            # Count priorities
            priority = notice.get('priority', 'Medium')
            stats["priority_breakdown"][priority] = stats["priority_breakdown"].get(priority, 0) + 1
            
            # Count categories
            category = notice.get('category', 'General')
            stats["category_breakdown"][category] = stats["category_breakdown"].get(category, 0) + 1
            
            # Count pinned
            if notice.get('is_pinned'):
                stats["pinned_count"] += 1
            
            # Expiry calculations
            expires_at = notice.get('expires_at')
            if expires_at:
                try:
                    expiry_date = datetime.fromisoformat(expires_at.replace('Z', '+00:00')).date()
                    today = datetime.now().date()
                    
                    if expiry_date < today:
                        stats["expired_count"] += 1
                    else:
                        days = (expiry_date - today).days
                        if 0 <= days <= 7:
                            stats["expiring_soon_count"] += 1
                except:
                    pass
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))