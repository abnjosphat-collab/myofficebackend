# PPE 2027 budget (Batch 3): stored settings plus the year's working-day calendar.
#
# The calculation itself lives in the frontend (`app/ppe/budget/budgetCalc.ts`).
# This router only stores the manager-edited settings in `ppe_budgets` (one row
# per year; the defaults live in code and nothing is seeded) and serves the
# calendar built from `app/zim_holidays.py`, so the screen and the downloads
# use the same working days. Everyone signed in may read (department-scoped viewers see nothing here); only managers save,
# and a save is refused with 409 when the row changed since it was opened.
from __future__ import annotations

import calendar as _calendar
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import get_current_user, require_role
from app.concurrency import assert_unchanged, touch
from app.errors import server_error
from app.scope import viewer_scope
from app.supabase_client import supabase, rows
from app.zim_holidays import zim_holidays_for_year

router = APIRouter()

RuleKind = Literal["per_working_day", "per_week", "per_month", "per_year", "matrix"]
WhoKind = Literal["everyone", "sections", "job_title"]


class Who(BaseModel):
    kind: WhoKind = "everyone"
    sections: list[str] = Field(default_factory=list)
    text: str = ""


class BudgetLine(BaseModel):
    key: str = Field(..., min_length=1)
    item: str = Field(..., min_length=1)
    rule: RuleKind
    qty_per_person: float = Field(..., ge=0)
    who: Who = Field(default_factory=Who)
    unit_price: Optional[float] = Field(default=None, ge=0)
    buffer_pct: float = Field(default=0, ge=0)


class StarterGroup(BaseModel):
    count: int = Field(..., ge=0)
    start_month: int = Field(..., ge=1, le=12)
    boilermakers: int = Field(..., ge=0)


class BudgetSettings(BaseModel):
    lines: list[BudgetLine] = Field(default_factory=list)
    confirmed: StarterGroup
    provisional: StarterGroup
    starter_kit: Optional[list[str]] = None
    add_gaps_in_january: bool = False
    show_provisional: bool = True


class BudgetSave(BaseModel):
    settings: BudgetSettings
    # The updated_at the Budget view was opened with; refused with 409 when stale.
    expected_updated_at: Optional[str] = None


def default_settings() -> dict:
    """The editable starting point: the owner's consumable list plus 10 starters."""
    def line(key, item, rule, qty, who=None):
        return {
            "key": key, "item": item, "rule": rule, "qty_per_person": qty,
            "who": who or {"kind": "everyone", "sections": [], "text": ""},
            "unit_price": None, "buffer_pct": 0,
        }

    boiler = {"kind": "job_title", "sections": [], "text": "Boilermaker"}
    return {
        "lines": [
            line("dust_masks", "Dust masks", "per_working_day", 1),
            line("artisan_gloves", "Artisan gloves", "per_week", 1),
            line("pvc_gloves", "PVC gloves", "per_week", 1),
            line("leather_gloves", "Leather gloves", "per_week", 1),
            line("chin_straps", "Chin straps", "per_month", 1),
            line("clear_goggles", "Clear safety goggles", "per_month", 1),
            line("dark_goggles", "Dark goggles", "per_month", 1, boiler),
        ],
        "confirmed": {"count": 5, "start_month": 1, "boilermakers": 0},
        "provisional": {"count": 5, "start_month": 1, "boilermakers": 0},
        "starter_kit": None,
        "add_gaps_in_january": False,
        "show_provisional": True,
    }


def year_calendar(year: int) -> dict:
    """Working-day calendar for the year: Mon–Fri minus Zimbabwe public holidays.

    `weeks` per month counts the Mondays in that month (one per week starting on
    a Monday: 4 or 5 a month, 52 or 53 a year). Holidays carry their names for
    the assumptions sheet.
    """
    holidays = zim_holidays_for_year(year)
    months = []
    for month in range(1, 13):
        days_in_month = _calendar.monthrange(year, month)[1]
        working_days = 0
        mondays = 0
        month_holidays = []
        for day in range(1, days_in_month + 1):
            d = date(year, month, day)
            if d.weekday() == 0:
                mondays += 1
            iso = d.isoformat()
            if iso in holidays:
                month_holidays.append({"date": iso, "name": holidays[iso]})
                continue
            if d.weekday() < 5:
                working_days += 1
        months.append({
            "month": month,
            "working_days": working_days,
            "weeks": mondays,
            "holidays": month_holidays,
        })
    return {
        "year": year,
        "months": months,
        "total_working_days": sum(m["working_days"] for m in months),
        "total_weeks": sum(m["weeks"] for m in months),
    }


def _require_engineering(current_user: dict) -> None:
    """The budget is Engineering-only: department-scoped viewers (Mining) get
    403 on read and save alike. Their people, records and starters are never
    part of it, and their managers must not overwrite Engineering's prices."""
    scope = viewer_scope(current_user)
    if scope is not None and scope["kind"] == "department":
        raise HTTPException(status_code=403, detail="The PPE budget is Engineering-only.")


def _check_year(year: int) -> None:
    if year < 2000 or year > 2100:
        raise HTTPException(status_code=422, detail="Year must be between 2000 and 2100.")


def _read_row(year: int) -> Optional[dict]:
    try:
        resp = supabase.table("ppe_budgets").select("*").eq("year", year).execute()
    except Exception as e:
        raise server_error(e, "Loading the budget")
    found = rows(resp)
    return found[0] if found else None


@router.get("/{year}")
async def get_budget(year: int, current_user: dict = Depends(get_current_user)):
    """Saved settings (or the defaults) with the year's calendar."""
    _check_year(year)
    _require_engineering(current_user)
    row = _read_row(year)
    return {
        "year": year,
        "settings": row["settings"] if row else default_settings(),
        "defaults": default_settings(),
        "calendar": year_calendar(year),
        "updated_at": row.get("updated_at") if row else None,
    }


@router.put("/{year}")
async def save_budget(year: int, payload: BudgetSave, current_user: dict = Depends(require_role("manager"))):
    """Save the manager-edited settings. Refused with 409 when stale."""
    _check_year(year)
    _require_engineering(current_user)
    existing = _read_row(year)
    assert_unchanged(existing or {}, payload.expected_updated_at)
    data = {"year": year, "settings": payload.settings.dict()}
    if existing:
        data["updated_by"] = current_user.get("email")
        touch(existing, data)
        try:
            resp = supabase.table("ppe_budgets").update(data).eq("year", year).execute()
        except Exception as e:
            raise server_error(e, "Saving the budget")
        saved = rows(resp)
        row = saved[0] if saved else {**existing, **data}
    else:
        data["updated_by"] = current_user.get("email")
        try:
            resp = supabase.table("ppe_budgets").insert(data).execute()
        except Exception as e:
            raise server_error(e, "Saving the budget")
        saved = rows(resp)
        row = saved[0] if saved else data
    return {"year": year, "settings": row.get("settings", data["settings"]), "updated_at": row.get("updated_at")}
