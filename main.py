# main.py - COMPLETE VERSION WITH STANDBY, SHEQ, NEAR MISS, WORK STOPPAGE, PTO, VFL, AND PACHEDU ROUTERS INTEGRATED
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
import logging
import traceback
import os
import sys
import uuid
from contextlib import asynccontextmanager

# Import supabase client (used only for health check, standby router uses its own import)
from app.supabase_client import supabase
from app.auth import get_current_user, require_role
from app.redis_client import ping_redis, close_redis
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.rate_limit import limiter

# Rate limiting — keyed by client IP, a single global default (see app/rate_limit.py)
# applied to every route via SlowAPIMiddleware, rather than a per-route
# @limiter.limit(...) decorator on all ~405 endpoints individually. This is the
# highest-leverage single change: it stops basic abuse/hammering across the whole
# API uniformly. A handful of the most sensitive endpoints (bulk delete, CSV/bulk
# import) get an additional, tighter decorator where they're defined.

# Sentry — error tracking. sentry-sdk has been a dependency all along but was never
# actually initialized anywhere, so no errors were ever being captured. Wired up
# conditionally: does nothing until SENTRY_DSN is set (a project-specific secret URL
# from sentry.io — not something that can be generated without a Sentry account), so
# this is safe to ship now and just start working the moment that env var is added.
SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1, send_default_pii=False)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ===== LIFESPAN CONTEXT MANAGER =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Starting MyOffice API...")
    if await ping_redis():
        logger.info("✅ Redis connection established")
    else:
        logger.warning("⚠️ Redis unreachable at startup — continuing without cache")
    yield
    # Shutdown
    await close_redis()
    logger.info("🛑 Shutting down MyOffice API...")

app = FastAPI(
    title="MyOffice API",
    version="1.0.0",
    description="Complete office management system with equipment, employees, and spares inventory",
    redirect_slashes=True,
    lifespan=lifespan
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: JSONResponse(
    status_code=429,
    content={"detail": f"Rate limit exceeded: {exc.detail}"},
))
app.add_middleware(SlowAPIMiddleware)

# CORS middleware — read allowed origins from env var (comma-separated) with sensible defaults.
#
# Previously this also matched allow_origin_regex=r"https://myoffice.*\.vercel\.app" with
# allow_methods/allow_headers=["*"] — the regex matches ANY domain containing "myoffice"
# before ".vercel.app" (e.g. "https://myoffice-evil-clone.vercel.app" would pass), and the
# wildcard methods/headers accept literally anything. Combined with allow_credentials=True
# (so cookies/auth headers are sent cross-origin), that's a broader trust boundary than
# intended. Now: only the explicit ALLOWED_ORIGINS list is trusted — add any new preview/
# deployment URL to that env var as needed rather than pattern-matching domain names — and
# only the methods/headers this API actually uses are allowed.
_raw_origins = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,"
    "https://myofficefrontend.vercel.app,https://myoffice-black.vercel.app"
)
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "X-Requested-With"],
)


# Last-resort handler for exceptions no route handled. FastAPI already turns
# HTTPException / RequestValidationError into proper responses (those have their own,
# more-specific handlers and take precedence), so this only fires for genuinely
# unexpected errors. Many handlers across the routers currently do
# `raise HTTPException(500, detail=f"Error: {str(e)}")`, which leaks the raw internal
# error string to the client; as those get simplified they can just let the exception
# propagate to here, which logs the full detail server-side (and to Sentry if
# configured) but returns a generic message — never an internal string or stack trace.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}")
    logger.error(traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )

# ===== BASIC ENDPOINTS THAT SHOULD ALWAYS WORK =====
@app.get("/")
async def root():
    return {
        "message": "MyOffice API is running!",
        "version": "1.0.0",
        "endpoints": {
            "health": "/api/health",
            "docs": "/docs",
            "daily_reports": "/api/daily-reports",
            "breakdowns": "/api/breakdowns",
            "standby": "/api/standby",
            "sheq": "/api/sheq",
            "nearmiss": "/api/nearmiss",
            "work_stoppage": "/api/work-stoppage",
            "pto": "/api/pto",
            "vfl": "/api/vfl",
            "pachedu": "/api/pachedu",
            "employees": "/api/employees",
            "equipment": "/api/equipment",
            "maintenance": "/api/maintenance",
            "spares": "/api/spares",
            "notices": "/api/notices",
            "availability": "/api/availabilities",
            "timesheets": "/api/timesheets",
            "requisitions": "/api/requisitions",
            "schedules": "/api/schedules",
            "safety_complaints": "/api/safety-complaints"
        }
    }

@app.get("/api/health")
async def health_check():
    try:
        standby_resp = supabase.table("standby_schedules").select("*", count="exact", head=True).execute()
        standby_count = standby_resp.count if hasattr(standby_resp, 'count') else 0
    except Exception as e:
        logger.error(f"Health check failed to get standby count: {e}")
        standby_count = 0

    redis_ok = await ping_redis()

    return {
        "status": "healthy",
        "message": "API is running",
        "timestamp": datetime.utcnow().isoformat(),
        "standby_schedules": standby_count,
        "redis": "connected" if redis_ok else "unavailable"
    }

@app.get("/api/debug-test")
async def debug_test():
    return {"message": "Debug test - working", "status": "success"}

# ===== ROUTER REGISTRATION =====
# Every router below used to get its own ~15-line try/except block: import, register,
# log success/failure, and on failure define hand-written GET/POST "fallback" endpoints
# that returned a fake 200 (or a 503 with the wrong shape) — a real import failure was
# invisible to callers, who'd see a plausible-looking response instead of an error.
#
# register_router() below is that same behavior, minus the fake success: a failure logs
# loudly (ERROR + full traceback) and is skipped. The router's endpoints then simply
# don't exist (a real 404), which is honest, instead of a fabricated 200. Every OTHER
# router keeps working — this app serves many independent domains (timesheets, PPE,
# SHEQ, maintenance...) and one broken router shouldn't take the rest down.
def register_router(module_name: str, prefix: str | None = None, tags: list[str] | None = None, key: str | None = None):
    # `key` is the loaded_routers dict key, when it needs to differ from the module
    # name — e.g. the notices module registers under "noticeboard" because that's
    # what the health-check/debug endpoints further down already look up.
    dict_key = key or module_name
    try:
        module = __import__(f"app.routers.{module_name}", fromlist=[module_name])
        router_obj = getattr(module, "router", None)
        if router_obj is None:
            raise ImportError(f"module app.routers.{module_name} has no 'router' attribute")
        kwargs = {}
        if prefix is not None:
            kwargs["prefix"] = prefix
        if tags is not None:
            kwargs["tags"] = tags
        app.include_router(router_obj, **kwargs)
        loaded_routers[dict_key] = router_obj
        logger.info(f"Router loaded: {dict_key}" + (f" at {prefix}" if prefix else ""))
    except Exception as e:
        logger.error(f"Failed to register router '{dict_key}': {e}")
        logger.error(traceback.format_exc())
        loaded_routers[dict_key] = None


loaded_routers: dict = {}

# Registered before the mock availability endpoints below — order matters here (see
# that section's comment) so it's preserved exactly as it was.
for _name, _prefix, _tags in [
    ("standby", None, None),
    ("sheq_inspections", None, None),
    ("near_miss", None, None),
    ("work_stoppage", None, None),
    ("pto", None, None),
    ("vfl", None, None),
    ("pachedu", None, None),
    ("safety_complaints", None, None),
    ("services", None, None),
    ("documents", None, None),
    ("photos", "/api", ["Photos"]),
    ("ai_safety", "/api", ["AI Safety"]),
]:
    register_router(_name, _prefix, _tags)

# ===== DIRECT AVAILABILITY ENDPOINTS (unchanged) =====
# NOTE (found while consolidating the registration above, not fixed — changes live
# route shapes and needs its own decision): these mock endpoints are registered
# BEFORE the real `availability` router below, so for any path they share exactly,
# these mock ones win and the real router's equivalent is unreachable. Also, in the
# second registration loop further down, "documents" (and compressors/inventory/
# training) get registered a second time with an /api/<name> prefix ON TOP OF a
# router that already defines that same prefix internally, producing doubled paths
# like /api/documents/api/documents/{id}. Both predate this cleanup and are
# preserved as-is here; worth a follow-up decision, not a silent fix.
class AvailabilityStats(BaseModel):
    totalEquipment: int = 0
    operational: int = 0
    inMaintenance: int = 0
    inBreakdown: int = 0
    overallAvailability: float = 0.0
    avgUptime: float = 0.0
    avgDowntime: float = 0.0
    totalOperationalHours: float = 0.0
    totalBreakdownHours: float = 0.0
    monthAvailability: float = 0.0
    weekAvailability: float = 0.0

mock_equipment_db = [
    {
        "id": "1",
        "name": "CNC Machine 1",
        "category": "Machinery",
        "department": "Production",
        "operationalHours": 450.5,
        "breakdownHours": 12.3,
        "availability": 97.27,
        "status": "operational",
        "lastMaintenance": "2024-01-15",
        "nextMaintenance": "2024-02-15",
        "uptime": 438.2,
        "downtime": 12.3,
        "mtbf": 120.5,
        "mttr": 2.5
    },
    {
        "id": "2",
        "name": "Forklift A",
        "category": "Vehicles",
        "department": "Logistics",
        "operationalHours": 320.0,
        "breakdownHours": 8.5,
        "availability": 97.34,
        "status": "operational",
        "lastMaintenance": "2024-01-10",
        "nextMaintenance": "2024-02-10",
        "uptime": 311.5,
        "downtime": 8.5,
        "mtbf": 85.3,
        "mttr": 3.2
    },
    {
        "id": "3",
        "name": "3D Printer",
        "category": "Electronics",
        "department": "R&D",
        "operationalHours": 280.0,
        "breakdownHours": 24.0,
        "availability": 91.43,
        "status": "maintenance",
        "lastMaintenance": "2024-01-20",
        "nextMaintenance": "2024-03-20",
        "uptime": 256.0,
        "downtime": 24.0,
        "mtbf": 65.7,
        "mttr": 6.5
    }
]

@app.get("/api/availabilities")
async def get_availabilities():
    logger.info("Fetching equipment availabilities...")
    return mock_equipment_db

@app.get("/api/availabilities/stats")
async def get_availability_stats():
    logger.info("Calculating availability statistics...")
    total_equipment = len(mock_equipment_db)
    operational = sum(1 for e in mock_equipment_db if e["status"] == "operational")
    in_maintenance = sum(1 for e in mock_equipment_db if e["status"] == "maintenance")
    in_breakdown = sum(1 for e in mock_equipment_db if e["status"] == "breakdown")
    
    total_operational_hours = sum(e["operationalHours"] for e in mock_equipment_db)
    total_breakdown_hours = sum(e["breakdownHours"] for e in mock_equipment_db)
    
    overall_availability = ((total_operational_hours - total_breakdown_hours) / total_operational_hours * 100) if total_operational_hours > 0 else 0
    
    avg_uptime = sum(e["uptime"] for e in mock_equipment_db) / total_equipment if total_equipment > 0 else 0
    avg_downtime = sum(e["downtime"] for e in mock_equipment_db) / total_equipment if total_equipment > 0 else 0
    
    return AvailabilityStats(
        totalEquipment=total_equipment,
        operational=operational,
        inMaintenance=in_maintenance,
        inBreakdown=in_breakdown,
        overallAvailability=round(overall_availability, 2),
        avgUptime=round(avg_uptime, 2),
        avgDowntime=round(avg_downtime, 2),
        totalOperationalHours=round(total_operational_hours, 2),
        totalBreakdownHours=round(total_breakdown_hours, 2),
        monthAvailability=round(overall_availability * 0.95, 2),
        weekAvailability=round(overall_availability * 0.98, 2)
    )

@app.get("/api/availabilities/{equipment_id}")
async def get_equipment_availability(equipment_id: str):
    for equipment in mock_equipment_db:
        if equipment["id"] == equipment_id:
            return equipment
    raise HTTPException(status_code=404, detail="Equipment not found")

@app.get("/api/availabilities/health/check")
async def availability_health_check():
    return {
        "status": "healthy",
        "service": "availability",
        "equipment_count": len(mock_equipment_db),
        "timestamp": datetime.utcnow().isoformat()
    }

# Registered after the mock availability endpoints above — same preserved ordering.
# "notices" registers under the "noticeboard" dict key — that's what the health-check
# and debug endpoints further down already look up.
for _name, _prefix, _tags, _key in [
    ("spares", "/api/spares", ["Spares"], None),
    ("daily_reports", "/api/daily-reports", ["Daily Reports"], None),
    ("breakdowns", None, None, None),
    ("notices", "/api/notices", ["Notices"], "noticeboard"),
    ("availability", "/api", ["Availability"], None),
    ("employees", "/api/employees", ["Employees"], None),
    ("admin", None, None, None),
    ("timesheets", "/api/timesheets", ["Timesheets"], None),
    ("requisitions", "/api/requisitions", ["Requisitions"], None),
    ("schedules", "/api/schedules", ["Schedules"], None),
    ("equipment", "/api/equipment", ["Equipment"], None),
    ("maintenance", "/api/maintenance", ["Maintenance"], None),
    ("issues", "/api/issues", ["Stock Issues"], None),
    ("drivers", "/api/drivers", ["Drivers"], None),
]:
    register_router(_name, _prefix, _tags, _key)

# ===== OTHER ROUTERS (unchanged) =====
routers_to_import = [
    "signatures", "usage",
    "reports", "inventory", "overtime", "ppe", "documents",
    "training", "visualization", "leaves", "compressors",
    # Engineering modules
    "job_cards", "handover", "compliance", "lubrication",
    "condition_monitoring", "contractors", "production",
    "failure_modes", "competency",
]

for router_name in routers_to_import:
    try:
        module = __import__(f"app.routers.{router_name}", fromlist=[router_name])
        
        router_obj = None
        if hasattr(module, 'router'):
            router_obj = getattr(module, 'router')
        else:
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, APIRouter):
                    router_obj = attr
                    break
        
        if router_obj is not None:
            prefix = f"/api/{router_name.replace('_', '-')}"
            app.include_router(router_obj, prefix=prefix, tags=[router_name.title().replace('_', ' ')])
            loaded_routers[router_name] = router_obj
            logger.info(f"✅ {router_name.title().replace('_', ' ')} router included at {prefix}")
        else:
            logger.warning(f"⚠️ No APIRouter found in {router_name} module")
            loaded_routers[router_name] = None
            
    except Exception as e:
        logger.error(f"❌ Failed to import {router_name} router: {e}")
        loaded_routers[router_name] = None

# ===== DIRECT NOTICE ENDPOINTS AS FALLBACK =====
logger.info("🔄 Adding direct notice endpoints as guaranteed fallback...")

class DirectNoticeCreate(BaseModel):
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    date: date
    category: str = Field(..., min_length=1)
    priority: str = Field(default="Medium")
    status: str = Field(default="Draft")
    is_pinned: bool = Field(default=False)
    author: Optional[str] = None
    department: Optional[str] = None
    expires_at: Optional[date] = None
    target_audience: Optional[str] = None
    notification_type: Optional[str] = None
    requires_acknowledgment: bool = Field(default=False)
    attachment_name: Optional[str] = None
    attachment_url: Optional[str] = None
    attachment_size: Optional[str] = None

# In-memory storage for notices as fallback
notices_db = []

@app.get("/api/direct-notices")
async def get_direct_notices():
    return notices_db

@app.post("/api/direct-notices")
async def create_direct_notice(notice: DirectNoticeCreate, current_user: dict = Depends(get_current_user)):
    notice_id = str(uuid.uuid4())
    new_notice = {
        "id": notice_id,
        **notice.dict(),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    notices_db.append(new_notice)
    return new_notice

@app.get("/api/direct-notices/{notice_id}")
async def get_direct_notice(notice_id: str):
    for notice in notices_db:
        if notice.get("id") == notice_id:
            return notice
    raise HTTPException(status_code=404, detail="Notice not found")

# ===== FALLBACK ROUTES FOR CRITICAL ENDPOINTS =====
@app.get("/api/spares")
@app.get("/api/spares/")
async def spares_fallback():
    if loaded_routers.get("spares"):
        return {
            "message": "Spares router is loaded",
            "use_endpoint": "/api/spares for full functionality"
        }
    else:
        return {
            "message": "Spares router not loaded",
            "status": "fallback_mode",
            "fix_steps": [
                "1. Check that app/routers/spares.py exists",
                "2. Check for syntax errors in spares.py",
                "3. Check supabase_client.py connection",
                "4. Restart the backend server"
            ]
        }

@app.get("/api/employees")
@app.get("/api/employees/")
async def employees_fallback():
    if loaded_routers.get("employees"):
        return {
            "message": "Employees router is loaded",
            "use_endpoint": "/api/employees for full functionality"
        }
    else:
        return {
            "message": "Employees router not loaded",
            "status": "fallback_mode",
            "note": "Check app/routers/employees.py for errors"
        }

# ===== DEBUG ENDPOINTS =====
@app.get("/api/debug-all-routes")
async def debug_all_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            routes.append({
                'path': route.path,
                'methods': list(route.methods),
                'name': getattr(route, 'name', 'N/A')
            })
    
    return {
        "all_routes": routes,
        "total_routes": len(routes),
        "spares_routes": [r for r in routes if 'spares' in r['path']],
        "standby_routes": [r for r in routes if 'standby' in r['path']],
        "sheq_routes": [r for r in routes if 'sheq' in r['path']],
        "nearmiss_routes": [r for r in routes if 'nearmiss' in r['path']],
        "work_stoppage_routes": [r for r in routes if 'work-stoppage' in r['path']],
        "pto_routes": [r for r in routes if 'pto' in r['path']],
        "vfl_routes": [r for r in routes if 'vfl' in r['path']],
        "pachedu_routes": [r for r in routes if 'pachedu' in r['path']],
        "notices_routes": [r for r in routes if 'notices' in r['path']],
        "direct_notices_routes": [r for r in routes if 'direct-notices' in r['path']],
        "employees_routes": [r for r in routes if 'employees' in r['path']],
        "equipment_routes": [r for r in routes if 'equipment' in r['path']],
        "maintenance_routes": [r for r in routes if 'maintenance' in r['path']],
        "availability_routes": [r for r in routes if 'availabilities' in r['path']],
        "timesheets_routes": [r for r in routes if 'timesheets' in r['path']],
        "requisitions_routes": [r for r in routes if 'requisitions' in r['path']],
        "routers_loaded": {k: v is not None for k, v in loaded_routers.items()}
    }

@app.get("/api/debug-router-status")
async def debug_router_status():
    return {
        "critical_routers": {
            "spares": loaded_routers.get("spares") is not None,
            "standby": True,
            "sheq": True,
            "nearmiss": True,
            "work_stoppage": True,
            "pto": True,
            "vfl": True,
            "pachedu": True,
            "noticeboard": loaded_routers.get("noticeboard") is not None,
            "availability": loaded_routers.get("availability") is not None,
            "employees": loaded_routers.get("employees") is not None,
            "daily_reports": loaded_routers.get("daily_reports") is not None,
            "breakdowns": loaded_routers.get("breakdowns") is not None,
            "equipment": loaded_routers.get("equipment") is not None,
            "maintenance": loaded_routers.get("maintenance") is not None,
            "timesheets": loaded_routers.get("timesheets") is not None,
            "requisitions": loaded_routers.get("requisitions") is not None
        },
        "all_routers": {k: v is not None for k, v in loaded_routers.items()},
        "direct_endpoints_available": {
            "notices": True,
            "standby": True,
            "sheq": True,
            "nearmiss": True,
            "work_stoppage": True,
            "pto": True,
            "vfl": True,
            "pachedu": True,
            "availability": True
        }
    }

# ===== HEALTH CHECK ENDPOINTS =====
@app.get("/api/spares/health")
async def spares_health_check_fallback():
    spares_router = loaded_routers.get("spares")
    if spares_router:
        return {
            "status": "router_loaded",
            "message": "Spares router is loaded",
            "use_endpoint": "/api/spares/health/check for detailed health"
        }
    else:
        return {
            "status": "router_not_loaded",
            "message": "Spares router failed to load",
            "fix_steps": [
                "1. Check that app/routers/spares.py exists",
                "2. Check for syntax errors in spares.py",
                "3. Check supabase_client.py connection",
                "4. Restart the backend server"
            ]
        }

@app.get("/api/availability/health")
async def availability_health_check_endpoint():
    return {
        "status": "healthy",
        "service": "availability",
        "direct_endpoints": [
            "GET /api/availabilities",
            "GET /api/availabilities/stats",
            "GET /api/availabilities/{id}",
            "GET /api/availabilities/health/check"
        ],
        "router_loaded": loaded_routers.get("availability") is not None,
        "mock_data_count": len(mock_equipment_db),
        "note": "Direct endpoints always available"
    }

@app.get("/api/employees/health")
async def employees_health_check():
    employees_router = loaded_routers.get("employees")
    if employees_router:
        return {
            "status": "healthy",
            "service": "employees",
            "message": "Employees router is loaded and ready"
        }
    else:
        return {
            "status": "unhealthy",
            "service": "employees",
            "message": "Employees router not loaded"
        }

@app.get("/api/notices/health")
async def notices_health_check():
    noticeboard_router = loaded_routers.get("noticeboard")
    if noticeboard_router:
        return {
            "status": "healthy",
            "service": "noticeboard",
            "message": "Noticeboard router is loaded and ready",
            "endpoints_available": [
                "GET /api/notices - Get all notices",
                "POST /api/notices - Create a notice",
                "GET /api/notices/{id} - Get a specific notice",
                "PUT /api/notices/{id} - Update a notice",
                "DELETE /api/notices/{id} - Delete a notice"
            ]
        }
    else:
        return {
            "status": "unhealthy_but_fallback_available",
            "service": "noticeboard",
            "message": "Noticeboard router not loaded, but fallback endpoints are available",
            "fallback_endpoints": [
                "GET /api/direct-notices - Get all notices",
                "POST /api/direct-notices - Create a notice",
                "GET /api/direct-notices/{id} - Get a specific notice"
            ],
            "fix_steps": [
                "1. Check that app/routers/notices.py exists",
                "2. Check supabase_client.py connection",
                "3. Check database table 'notices' exists in Supabase"
            ]
        }

@app.get("/api/timesheets/health")
async def timesheets_health_check():
    timesheets_router = loaded_routers.get("timesheets")
    if timesheets_router:
        return {
            "status": "healthy",
            "service": "timesheets",
            "message": "Timesheets router is loaded and ready",
            "endpoints_available": [
                "GET /api/timesheets - Get all timesheets",
                "POST /api/timesheets - Create a timesheet entry",
                "GET /api/timesheets/{id} - Get a specific timesheet",
                "PATCH /api/timesheets/{id} - Update a timesheet",
                "DELETE /api/timesheets/{id} - Delete a timesheet",
                "GET /api/timesheets/stats/summary - Get timesheet statistics"
            ]
        }
    else:
        return {
            "status": "unhealthy",
            "service": "timesheets",
            "message": "Timesheets router not loaded",
            "fix_steps": [
                "1. Check that app/routers/timesheets.py exists",
                "2. Rename overtime.py to timesheets.py if needed",
                "3. Check for syntax errors in timesheets.py",
                "4. Restart the backend server"
            ]
        }

@app.get("/api/requisitions/health")
async def requisitions_health_check():
    requisitions_router = loaded_routers.get("requisitions")
    if requisitions_router:
        return {
            "status": "healthy",
            "service": "requisitions",
            "message": "Requisitions router is loaded and ready",
            "endpoints_available": [
                "GET /api/requisitions - Get all requisitions",
                "POST /api/requisitions - Create a requisition",
                "GET /api/requisitions/{id} - Get a specific requisition",
                "PATCH /api/requisitions/{id} - Update a requisition",
                "DELETE /api/requisitions/{id} - Delete a requisition",
                "GET /api/requisitions/daily-total/{date} - Get daily total",
                "GET /api/requisitions/stats/summary - Get statistics"
            ]
        }
    else:
        return {
            "status": "unhealthy",
            "service": "requisitions",
            "message": "Requisitions router not loaded",
            "fix_steps": [
                "1. Check that app/routers/requisitions.py exists",
                "2. Check for syntax errors in requisitions.py",
                "3. Check supabase_client.py connection",
                "4. Check database tables 'requisitions' and 'requisition_items' exist",
                "5. Restart the backend server"
            ]
        }

# ===== TEST ENDPOINTS =====
@app.get("/api/test-availability-connection")
async def test_availability_connection():
    return {
        "status": "test",
        "message": "Availability connection test",
        "direct_endpoints_active": True,
        "equipment_count": len(mock_equipment_db),
        "backend": "FastAPI",
        "endpoints_available": [
            "GET /api/availabilities",
            "GET /api/availabilities/stats",
            "GET /api/availabilities/{id}",
            "GET /api/availabilities/health/check"
        ],
        "note": "Availability system is working with mock data"
    }

@app.get("/api/test-all-connections")
async def test_all_connections():
    test_results = {}
    
    # Test basic health
    try:
        response = await health_check()
        test_results["basic_health"] = {"status": "success", "data": response}
    except Exception as e:
        test_results["basic_health"] = {"status": "error", "error": str(e)}
    
    # Test each critical router
    critical_routers = ["spares", "noticeboard", "availability", "employees", 
                        "daily_reports", "breakdowns", "equipment", "maintenance", 
                        "timesheets", "requisitions", "sheq", "nearmiss", 
                        "work_stoppage", "pto", "vfl", "pachedu"]
    
    for router_name in critical_routers:
        router = loaded_routers.get(router_name)
        test_results[router_name] = {
            "loaded": router is not None,
            "endpoints": []
        }
        
        if router:
            try:
                # Try to get routes from router
                routes = []
                for route in router.routes:
                    if hasattr(route, 'methods') and hasattr(route, 'path'):
                        methods = list(route.methods) if hasattr(route, 'methods') else []
                        routes.append(f"{methods} {route.path}")
                
                test_results[router_name]["endpoints"] = routes[:5]
                test_results[router_name]["total_endpoints"] = len(routes)
            except Exception as e:
                test_results[router_name]["error"] = str(e)
    
    # Test direct endpoints
    test_results["direct_availability"] = {
        "available": True,
        "endpoints": [
            "GET /api/availabilities",
            "GET /api/availabilities/stats",
            "GET /api/availabilities/{id}",
            "GET /api/availabilities/health/check"
        ]
    }
    
    test_results["direct_standby"] = {
        "available": True,
        "endpoints": [
            "GET /api/standby",
            "POST /api/standby",
            "GET /api/standby/{id}"
        ]
    }
    
    test_results["direct_sheq"] = {
        "available": True,
        "endpoints": [
            "GET /api/sheq",
            "POST /api/sheq",
            "GET /api/sheq/{id}",
            "GET /api/sheq/stats/overview"
        ]
    }
    
    test_results["direct_nearmiss"] = {
        "available": True,
        "endpoints": [
            "GET /api/nearmiss",
            "POST /api/nearmiss",
            "GET /api/nearmiss/{id}",
            "GET /api/nearmiss/stats/overview"
        ]
    }
    
    test_results["direct_work_stoppage"] = {
        "available": True,
        "endpoints": [
            "GET /api/work-stoppage",
            "POST /api/work-stoppage",
            "GET /api/work-stoppage/{id}",
            "PATCH /api/work-stoppage/{id}",
            "DELETE /api/work-stoppage/{id}",
            "GET /api/work-stoppage/stats/overview",
            "GET /api/work-stoppage/suggestions/departments",
            "GET /api/work-stoppage/suggestions/inspectors"
        ]
    }
    
    test_results["direct_pto"] = {
        "available": True,
        "endpoints": [
            "GET /api/pto",
            "POST /api/pto",
            "GET /api/pto/{id}",
            "PATCH /api/pto/{id}",
            "DELETE /api/pto/{id}",
            "GET /api/pto/stats/overview",
            "GET /api/pto/suggestions/observers"
        ]
    }
    
    test_results["direct_vfl"] = {
        "available": True,
        "endpoints": [
            "GET /api/vfl",
            "POST /api/vfl",
            "GET /api/vfl/{id}",
            "PATCH /api/vfl/{id}",
            "DELETE /api/vfl/{id}",
            "GET /api/vfl/stats/overview",
            "GET /api/vfl/suggestions/observers"
        ]
    }
    
    test_results["direct_pachedu"] = {
        "available": True,
        "endpoints": [
            "GET /api/pachedu",
            "POST /api/pachedu",
            "GET /api/pachedu/{id}",
            "PATCH /api/pachedu/{id}",
            "DELETE /api/pachedu/{id}",
            "GET /api/pachedu/stats/overview",
            "GET /api/pachedu/suggestions/departments"
        ]
    }
    
    test_results["direct_notices"] = {
        "available": True,
        "endpoints": [
            "GET /api/direct-notices",
            "POST /api/direct-notices",
            "GET /api/direct-notices/{id}"
        ]
    }
    
    return {
        "status": "test_complete",
        "timestamp": datetime.utcnow().isoformat(),
        "results": test_results
    }

# ===== STARTUP EVENT =====
@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Application starting up...")
    
    # Log loaded routers
    loaded_count = sum(1 for router in loaded_routers.values() if router is not None)
    total_count = len(loaded_routers)
    logger.info(f"📈 Router loading summary: {loaded_count}/{total_count} routers loaded")
    
    # Check critical routers
    critical_routers = {
        "spares": "Spares Inventory",
        "noticeboard": "Noticeboard Management",
        "availability": "Equipment Availability",
        "employees": "Employees",
        "daily_reports": "Daily Reports",
        "breakdowns": "Breakdowns",
        "equipment": "Equipment",
        "maintenance": "Maintenance",
        "timesheets": "Timesheets",
        "requisitions": "Requisitions",
        "sheq": "SHEQ Inspections",
        "nearmiss": "Near Miss Reports",
        "work_stoppage": "Work Stoppage Reports",
        "pto": "Planned Task Observation",
        "vfl": "Visible Felt Leadership",
        "pachedu": "Pachedu Care Observations"
    }
    
    for router_name, display_name in critical_routers.items():
        if loaded_routers.get(router_name):
            logger.info(f"✅ {display_name} router SUCCESSFULLY LOADED!")
        else:
            logger.error(f"❌ {display_name} router FAILED TO LOAD!")
    
    # Log standalone endpoints status
    logger.info("📊 Standalone Systems Status:")
    logger.info(f"   ✅ Availability endpoints available at /api/availabilities")
    logger.info(f"   📋 Currently {len(mock_equipment_db)} equipment in availability system")
    logger.info(f"   ✅ Standby endpoints available at /api/standby")
    try:
        count_resp = supabase.table("standby_schedules").select("*", count="exact", head=True).execute()
        count = count_resp.count if hasattr(count_resp, 'count') else 0
        logger.info(f"   📋 Currently {count} schedules in standby system")
    except:
        logger.info(f"   📋 Standby schedules count unavailable")
    logger.info(f"   ✅ SHEQ endpoints available at /api/sheq")
    logger.info(f"   ✅ Near Miss endpoints available at /api/nearmiss")
    logger.info(f"   ✅ Work Stoppage endpoints available at /api/work-stoppage")
    logger.info(f"   ✅ PTO endpoints available at /api/pto")
    logger.info(f"   ✅ VFL endpoints available at /api/vfl")
    logger.info(f"   ✅ Pachedu endpoints available at /api/pachedu")
    logger.info(f"   ✅ Direct notice endpoints available at /api/direct-notices")
    logger.info(f"   📋 Currently {len(notices_db)} notices in fallback system")
    logger.info(f"   ✅ Timesheets endpoints available at /api/timesheets")
    
    if loaded_routers.get("requisitions"):
        logger.info(f"   ✅ Requisitions endpoints available at /api/requisitions")
    else:
        logger.error(f"   ❌ REQUISITIONS ROUTER FAILED TO LOAD - CHECK app/routers/requisitions.py")
    
    # Log all routes for debugging
    logger.info("📋 All registered routes by category:")
    
    route_categories = {
        "spares": [],
        "standby": [],
        "sheq": [],
        "nearmiss": [],
        "work_stoppage": [],
        "pto": [],
        "vfl": [],
        "pachedu": [],
        "notices": [],
        "direct_notices": [],
        "availability": [],
        "employees": [],
        "equipment": [],
        "maintenance": [],
        "daily_reports": [],
        "breakdowns": [],
        "timesheets": [],
        "requisitions": [],
        "other": []
    }
    
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            methods = list(route.methods) if hasattr(route, 'methods') else []
            path_info = f"{methods} {route.path}"
            
            categorized = False
            for category in route_categories.keys():
                if f'/api/{category.replace("_", "-")}' in str(route.path):
                    route_categories[category].append(path_info)
                    categorized = True
                    break
            
            if not categorized and '/api/' in str(route.path):
                route_categories["other"].append(path_info)
    
    for category, routes in route_categories.items():
        if routes:
            logger.info(f"📝 {category.replace('_', ' ').title()} routes ({len(routes)}):")
            for route in routes[:3]:
                logger.info(f"   {route}")
            if len(routes) > 3:
                logger.info(f"   ... and {len(routes) - 3} more")
    
    # Log total endpoints
    total_endpoints = sum(len(routes) for routes in route_categories.values())
    logger.info(f"📊 Total endpoints registered: {total_endpoints}")
    
    # Special notice for availability routes
    if route_categories["availability"]:
        logger.info("📊 Availability System is ready at:")
        for route in route_categories["availability"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for timesheets routes
    if route_categories["timesheets"]:
        logger.info("📊 Timesheets System is ready at:")
        for route in route_categories["timesheets"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for requisitions routes
    if route_categories["requisitions"]:
        logger.info("📊 Requisitions System is ready at:")
        for route in route_categories["requisitions"][:5]:
            logger.info(f"   {route}")
    else:
        logger.error(f"❌❌❌ NO REQUISITIONS ROUTES FOUND! ❌❌❌")
    
    # Special notice for SHEQ routes
    if route_categories["sheq"]:
        logger.info("📊 SHEQ System is ready at:")
        for route in route_categories["sheq"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for Near Miss routes
    if route_categories["nearmiss"]:
        logger.info("📊 Near Miss System is ready at:")
        for route in route_categories["nearmiss"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for Work Stoppage routes
    if route_categories["work_stoppage"]:
        logger.info("📊 Work Stoppage System is ready at:")
        for route in route_categories["work_stoppage"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for PTO routes
    if route_categories["pto"]:
        logger.info("📊 PTO System is ready at:")
        for route in route_categories["pto"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for VFL routes
    if route_categories["vfl"]:
        logger.info("📊 VFL System is ready at:")
        for route in route_categories["vfl"][:5]:
            logger.info(f"   {route}")
    
    # Special notice for Pachedu routes
    if route_categories["pachedu"]:
        logger.info("📊 Pachedu System is ready at:")
        for route in route_categories["pachedu"][:5]:
            logger.info(f"   {route}")

# ===== SHUTDOWN EVENT =====
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Application shutting down...")

# ===== VERCEL HANDLER =====
from mangum import Mangum
handler = Mangum(app)

logger.info("🏁 Main.py setup completed - Standby, SHEQ, Near Miss, Work Stoppage, PTO, VFL, and Pachedu routers integrated, other routers as before")