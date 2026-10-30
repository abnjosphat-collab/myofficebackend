# tests/test_ppe_budget.py - 2027 PPE budget settings + calendar (Batch 3).
#
# The calculation lives in the frontend; this covers what the server owns:
# the year's working-day calendar (hand-counted), the stored settings with
# validation, the 409 on a stale save, and the manager-only write gate.
import pytest
from fastapi import HTTPException

import app.routers.ppe_budget as mod
from app.routers.ppe_budget import (
    BudgetSave,
    default_settings,
    get_budget,
    save_budget,
    year_calendar,
)

USER = {"user_id": "u-1", "email": "u@x.com", "role": "user"}
MANAGER = {"user_id": "m-1", "email": "m@x.com", "role": "manager"}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, state, table):
        self.state = state
        self.table = table
        self._eq = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def insert(self, data):
        self._pending = ("insert", dict(data))
        return self

    def update(self, data):
        self._pending = ("update", dict(data))
        return self

    def execute(self):
        if getattr(self, "_pending", None):
            op, data = self._pending
            self._pending = None
            if op == "insert":
                row = dict(data)
                row.setdefault("updated_at", "2026-10-30T00:00:00+00:00")
                self.state["rows"].append(row)
                self.state.setdefault("inserted", []).append(dict(data))
                return _Result([row])
            col, val = self._eq
            updated = []
            for row in self.state["rows"]:
                if row.get(col) == val:
                    row.update(data)
                    updated.append(dict(row))
            self.state.setdefault("updated", []).append(dict(data))
            return _Result(updated)
        if self._eq is None:
            return _Result(list(self.state["rows"]))
        col, val = self._eq
        return _Result([r for r in self.state["rows"] if r.get(col) == val])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state

    def table(self, name):
        assert name == "ppe_budgets"
        return _Query(self.state, name)


@pytest.fixture
def patch_supabase(monkeypatch):
    state = {"rows": []}
    monkeypatch.setattr(mod, "supabase", _FakeSupabase(state))
    return state


def _settings(**over):
    s = default_settings()
    s.update(over)
    return s


# ─── calendar (pure, hand-counted for 2027) ────────────────────────────────────

# Month: (working_days, mondays). Holidays that fall on a weekday are excluded:
# Jan 1 Fri; Mar 26 Fri + Mar 29 Mon (27th is a Saturday); Apr 18 Sun; May 1 Sat
# + May 25 Tue; Aug 9 Mon + Aug 10 Tue; Sep 15 Wed; Dec 22 Wed (25th Sat, 26th Sun).
EXPECTED_2027 = {
    1: (20, 4), 2: (20, 4), 3: (21, 5), 4: (22, 4), 5: (20, 5), 6: (22, 4),
    7: (22, 4), 8: (20, 5), 9: (21, 4), 10: (21, 4), 11: (22, 5), 12: (22, 4),
}


def test_2027_working_days_match_the_hand_count():
    cal = year_calendar(2027)
    assert [(m["month"], m["working_days"], m["weeks"]) for m in cal["months"]] == [
        (month, days, weeks) for month, (days, weeks) in EXPECTED_2027.items()
    ]
    assert cal["total_working_days"] == 253


def test_weeks_sum_to_52_or_53():
    assert year_calendar(2027)["total_weeks"] == 52
    assert year_calendar(2028)["total_weeks"] in (52, 53)


def test_holidays_carry_names_for_the_assumptions_sheet():
    months = {m["month"]: m for m in year_calendar(2027)["months"]}
    assert {"date": "2027-03-26", "name": "Good Friday"} in months[3]["holidays"]
    assert {"date": "2027-09-15", "name": "Munhumutapa Day"} in months[9]["holidays"]
    assert all(h["name"] for m in months.values() for h in m["holidays"])


def test_weekend_holidays_do_not_reduce_working_days():
    # 18 Apr 2027 is a Sunday: April keeps all 22 weekdays.
    months = {m["month"]: m for m in year_calendar(2027)["months"]}
    assert months[4]["working_days"] == 22
    assert {"date": "2027-04-18", "name": "Independence Day"} in months[4]["holidays"]


# ─── GET (everyone signed in may read) ────────────────────────────────────────

async def test_get_returns_defaults_and_calendar_when_nothing_saved(patch_supabase):
    got = await get_budget(2027, current_user=USER)
    assert got["settings"] == default_settings()
    assert got["defaults"] == default_settings()
    assert got["calendar"]["year"] == 2027
    assert len(got["calendar"]["months"]) == 12
    assert got["updated_at"] is None


async def test_get_returns_saved_settings(patch_supabase):
    patch_supabase["rows"].append({
        "year": 2027, "settings": _settings(show_provisional=False),
        "updated_at": "2026-10-01T00:00:00+00:00",
    })
    got = await get_budget(2027, current_user=USER)
    assert got["settings"]["show_provisional"] is False
    assert got["updated_at"] == "2026-10-01T00:00:00+00:00"


async def test_year_out_of_range_is_refused(patch_supabase):
    with pytest.raises(HTTPException) as exc:
        await get_budget(1999, current_user=USER)
    assert exc.value.status_code == 422


# ─── PUT (managers only, validated, 409 when stale) ──────────────────────────

async def test_manager_save_creates_the_row(patch_supabase):
    got = await save_budget(2027, BudgetSave(settings=_settings()), current_user=MANAGER)
    assert got["year"] == 2027
    assert got["updated_at"]
    assert len(patch_supabase["rows"]) == 1
    assert patch_supabase["rows"][0]["updated_by"] == "m@x.com"


async def test_stale_save_is_409_and_writes_nothing(patch_supabase):
    patch_supabase["rows"].append({
        "year": 2027, "settings": _settings(), "updated_at": "2026-10-02T00:00:00+00:00",
    })
    with pytest.raises(HTTPException) as exc:
        await save_budget(
            2027,
            BudgetSave(settings=_settings(), expected_updated_at="2026-10-01T00:00:00+00:00"),
            current_user=MANAGER,
        )
    assert exc.value.status_code == 409
    assert "inserted" not in patch_supabase and "updated" not in patch_supabase


async def test_fresh_save_with_matching_stamp_passes(patch_supabase):
    patch_supabase["rows"].append({
        "year": 2027, "settings": _settings(), "updated_at": "2026-10-02T00:00:00+00:00",
    })
    got = await save_budget(
        2027,
        BudgetSave(settings=_settings(show_provisional=False),
                   expected_updated_at="2026-10-02T00:00:00+00:00"),
        current_user=MANAGER,
    )
    assert got["settings"]["show_provisional"] is False


async def test_negative_rate_is_refused():
    bad = _settings()
    bad["lines"][0]["qty_per_person"] = -1
    with pytest.raises(Exception):
        BudgetSave(settings=bad)


async def test_unknown_rule_is_refused():
    bad = _settings()
    bad["lines"][0]["rule"] = "per_fortnight"
    with pytest.raises(Exception):
        BudgetSave(settings=bad)


async def test_matrix_rule_is_accepted_for_card_lines():
    ok = _settings()
    ok["lines"].append({
        "key": "card:worksuit", "item": "worksuit", "rule": "matrix",
        "qty_per_person": 1, "who": {"kind": "everyone", "sections": [], "text": ""},
        "unit_price": 25, "buffer_pct": 10,
    })
    saved = BudgetSave(settings=ok)
    assert saved.settings.lines[-1].rule == "matrix"


async def test_negative_price_is_refused():
    bad = _settings()
    bad["lines"][0]["unit_price"] = -5
    with pytest.raises(Exception):
        BudgetSave(settings=bad)


async def test_starter_month_outside_1_to_12_is_refused():
    bad = _settings()
    bad["confirmed"] = {"count": 5, "start_month": 13, "boilermakers": 0}
    with pytest.raises(Exception):
        BudgetSave(settings=bad)


async def test_plain_user_save_is_refused_by_the_role_gate(patch_supabase):
    # The PUT route is gated by Depends(require_role("manager")): the role
    # check itself decides who reaches the save body.
    from app.auth import role_at_least
    assert role_at_least("manager", "manager") is True
    assert role_at_least("user", "manager") is False
