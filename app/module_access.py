# backend/app/module_access.py — per-account module limit (Batch 4, Mining).
#
# An account whose user_profiles.permissions holds a `modules` list (frontend
# hrefs, e.g. ["/employees", "/ppe"]) may only call the routers mapped here;
# anything else answers 403. Accounts WITHOUT modules are unchanged, and a
# request with no signed-in user (anonymous usage ingestion, open reads)
# passes through — the route's own auth decides those.
#
# Keys are main.py's loaded_routers dict keys (the `key=` override when
# present, else the module name). A router missing from this map fails CLOSED
# for limited accounts (it is treated as its own href, which no account
# lists) and fails the route-guard test, so a new router cannot slip through
# unmapped.
from typing import Optional

from fastapi import Header, HTTPException

from app.auth import get_current_user_optional

MODULE_LIMIT_MESSAGE = "This account can only use Employees and PPE."

#: Router dict key -> frontend href, or None for always-allowed shared
#: plumbing: the user's own profile (signatures serves /me), settings
#: pick-lists, and the photo uploader every page shares.
ROUTER_MODULES: dict[str, Optional[str]] = {
    # The Mining account's two modules (the Excel importer lives on the
    # Employees page but stays admin-only through require_role).
    "employees": "/employees",
    "employee_import": "/employees",
    "ppe": "/ppe",
    "ppe_order_list": "/ppe",
    "ppe_budget": "/ppe",
    # Always allowed: own profile, settings pick-lists, shared photo upload.
    "signatures": None,
    "lookup_lists": None,
    "photos": None,
    # Company data, one module each. Routers without their own page ride
    # with the page that calls them (schedules/emaint_import live in
    # maintenance, production/lubrication in the engineering report,
    # duty rosters on the standby page, handover under shifts).
    "standby": "/standby",
    "duty_roster": "/standby",
    "standby_rotations": "/standby",
    "duty_rotations": "/standby",
    "rotation_covers": "/standby",
    "handover": "/shifts",
    "sheq_inspections": "/sheq_inspection",
    "near_miss": "/near_miss",
    "work_stoppage": "/work_stoppage",
    "pto": "/pto",
    "vfl": "/vfl",
    "pachedu": "/pachedu",
    "safety_complaints": "/safety_complaints",
    "ai_safety": "/sheq",
    "services": "/services",
    "documents": "/documents",
    "document_folders": "/documents",
    "sops": "/sop-library",
    "spares": "/spares",
    "daily_reports": "/engineering_report",
    "production": "/engineering_report",
    "lubrication": "/engineering_report",
    "breakdowns": "/breakdowns",
    "noticeboard": "/noticeboard",
    "availability": "/availability",
    "admin": "/admin",
    "audit": "/admin",
    "timesheets": "/timesheets",
    "nec_timesheet_import": "/timesheets",
    "artisan_timesheets": "/artisan-timesheets",
    "requisitions": "/requisitions",
    "failure_modes": "/reliability",
    "schedules": "/maintenance",
    "equipment": "/equipment",
    "maintenance": "/maintenance",
    "emaint_import": "/maintenance",
    "issues": "/issues",
    "tools_workspace": "/tools",
    "tools_workspace_set_types": "/tools",
    "drivers": "/drivers",
    "saved_quotations": "/quotations",
    "tasks_events": "/tasks-events",
    "usage": "/usage-analyzer",
    "inventory": "/inventory",
    "overtime": "/overtime",
    "training": "/training",
    "leaves": "/leaves",
    "compressors": "/compressors",
    "job_cards": "/job-cards",
    "compliance": "/compliance-register",
    "condition_monitoring": "/condition-monitoring",
    "contractors": "/contractors",
    "competency": "/competency",
    "recently_deleted": "/recently-deleted",
}


def require_module_access(module: Optional[str]):
    """Gate a router to one module href. None (shared plumbing) always
    passes; anonymous callers pass (the route's own auth decides); accounts
    without a modules list pass unchanged; members pass when their list
    holds the module, else 403."""
    async def _check(authorization: Optional[str] = Header(None)):
        if module is None:
            return
        user = await get_current_user_optional(authorization)
        if user is None:
            return
        allowed = user.get("modules")
        if allowed is None:
            return
        if isinstance(allowed, list) and module in allowed:
            return
        raise HTTPException(status_code=403, detail=MODULE_LIMIT_MESSAGE)
    return _check
