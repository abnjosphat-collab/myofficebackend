# main.py - COMPLETE VERSION WITH STANDBY, SHEQ, NEAR MISS, WORK STOPPAGE, PTO, VFL, AND PACHEDU ROUTERS INTEGRATED
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional, List
from datetime import datetime, date
import logging
import traceback
import os
import sys
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
def register_router(module_name: str, prefix: str | None = None, tags: list[str] | None = None, key: str | None = None, dependencies: list | None = None):
    # `key` is the loaded_routers dict key, when it needs to differ from the module
    # name — e.g. the notices module registers under "noticeboard" because that's
    # what the health-check/debug endpoints further down already look up.
    # `dependencies` gates every route the router contributes (including nested
    # include_router() mounts) — e.g. accounting's manager+-only gate below.
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
        if dependencies is not None:
            kwargs["dependencies"] = dependencies
        app.include_router(router_obj, **kwargs)
        loaded_routers[dict_key] = router_obj
        logger.info(f"Router loaded: {dict_key}" + (f" at {prefix}" if prefix else ""))
    except Exception as e:
        logger.error(f"Failed to register router '{dict_key}': {e}")
        logger.error(traceback.format_exc())
        loaded_routers[dict_key] = None


loaded_routers: dict = {}

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
    ("lookup_lists", "/api/lookup-lists", ["Lookup Lists"], None),
]:
    register_router(_name, _prefix, _tags, _key)

# Manager+ only, whole router — company financials. Can't go in the generic
# routers_to_import list below (that loop only derives prefix/tags, no support
# for extra kwargs like dependencies).
register_router("accounting", "/api/accounting", ["Accounting"],
                 dependencies=[Depends(require_role('manager'))])

# Manager+ only, whole router — same shape as accounting above.
register_router("tasks_events", "/api/tasks-events", ["Tasks & Events"],
                 dependencies=[Depends(require_role('manager'))])

# ===== OTHER ROUTERS =====
# "documents" is deliberately absent here — it's already registered above (the
# no-prefix entry in the first table), and its own router already carries
# prefix="/api/documents" internally. This loop used to register it a SECOND
# time with an extra /api/documents prefix layered on top, which mounted every
# documents route twice: once correctly at /api/documents/*, and once more at
# the doubled, unreachable-by-the-frontend /api/documents/api/documents/*.
for _name in [
    "signatures", "usage",
    "reports", "inventory", "overtime", "ppe",
    "training", "visualization", "leaves", "compressors",
    # Engineering modules
    "job_cards", "handover", "compliance", "lubrication",
    "condition_monitoring", "contractors", "production",
    "failure_modes", "competency",
]:
    register_router(_name, f"/api/{_name.replace('_', '-')}", [_name.title().replace('_', ' ')])

# ===== VERCEL HANDLER =====
from mangum import Mangum
handler = Mangum(app)

logger.info("🏁 Main.py setup completed - Standby, SHEQ, Near Miss, Work Stoppage, PTO, VFL, and Pachedu routers integrated, other routers as before")