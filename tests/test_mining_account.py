# tests/test_mining_account.py — Batch 4: the Mining department account.
#
# Department scope ({"department": "Mining"} stored in permissions.scope),
# the modules limit (permissions.modules, enforced in main.py's mount loop),
# and the kept-apart defaults (unscoped lists leave Mining out unless
# ?department=Mining). Router tests mount REAL routers on the shared harness
# fake so "refused" also proves "nothing written".
import copy

import pytest
from fastapi import HTTPException

from app import auth as auth_module
from app.auth import _parse_limits
from tests.scope_harness import mount_router, FakeSupabase

MINING = {"user_id": "u-min", "email": "miner@example.com", "role": "user",
          "scope": {"department": "Mining"}}
MINING_MGR = {"user_id": "u-minmgr", "email": "minmgr@example.com", "role": "manager",
              "scope": {"department": "Mining"}}
ENG_MGR = {"user_id": "u-eng", "email": "eng@example.com", "role": "manager", "scope": None}
ENG_USER = {"user_id": "u-engu", "email": "engu@example.com", "role": "user", "scope": None}
PROJECTS = {"user_id": "u-proj", "email": "proj@example.com", "role": "user", "scope": "Projects"}


# ─── auth: the limits RPC with legacy fallback ───────────────────────────────

class _AuthUser:
    def __init__(self, uid="u-9", email="x@y.com"):
        self.id = uid
        self.email = email
        self.factors = []


class _FakeAuth:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R:
            pass
        r = R()
        r.user = self._user
        return r


class _RpcResult:
    def __init__(self, data):
        self.data = data


class _LimitsFake:
    """Fake supabase for get_current_user: the role RPC, the limits RPC and
    the legacy scope RPC, each independently fail-able."""

    def __init__(self, user, role="user", limits=None, legacy_scope=None, fail=()):
        self.auth = _FakeAuth(user)
        self._role = role
        self._limits = limits
        self._legacy_scope = legacy_scope
        self._fail = set(fail)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append(name)
        if name in self._fail:
            raise RuntimeError(f"{name} unavailable")
        self._last = name
        return self

    def execute(self):
        if self._last == "get_user_limits_by_id":
            return _RpcResult(self._limits)
        if self._last == "get_user_scope_by_id":
            return _RpcResult(self._legacy_scope)
        return _RpcResult(self._role)


def _patch_auth(monkeypatch, **kwargs):
    monkeypatch.setattr(auth_module, "supabase",
                        _LimitsFake(_AuthUser(), **kwargs))


async def test_limits_rpc_carries_dict_scope_and_modules(monkeypatch):
    _patch_auth(monkeypatch, role="user",
                limits={"scope": {"department": "Mining"}, "modules": ["/employees", "/ppe"]})
    result = await auth_module.get_current_user("Bearer [REDACTED]")
    assert result["scope"] == {"department": "Mining"}
    assert result["modules"] == ["/employees", "/ppe"]
    assert result["role"] == "user"


async def test_limits_rpc_string_scope_and_null_modules(monkeypatch):
    _patch_auth(monkeypatch, role="manager",
                limits={"scope": "Projects", "modules": None})
    result = await auth_module.get_current_user("Bearer [REDACTED]")
    assert result["scope"] == "Projects"
    assert result["modules"] is None


async def test_limits_rpc_missing_row_is_unscoped_without_modules(monkeypatch):
    _patch_auth(monkeypatch, role="user", limits=None)
    result = await auth_module.get_current_user("Bearer [REDACTED]")
    assert result["scope"] is None
    assert result["modules"] is None


async def test_missing_limits_function_falls_back_to_legacy_scope(monkeypatch):
    fake = _LimitsFake(_AuthUser(), role="manager", legacy_scope="Projects",
                       fail=("get_user_limits_by_id",))
    monkeypatch.setattr(auth_module, "supabase", fake)
    result = await auth_module.get_current_user("Bearer [REDACTED]")
    assert result["scope"] == "Projects"
    assert result["modules"] is None
    assert "get_user_scope_by_id" in fake.calls


async def test_both_lookups_failing_is_a_loud_503_not_a_silent_downgrade(monkeypatch):
    _patch_auth(monkeypatch, fail=("get_user_limits_by_id", "get_user_scope_by_id"))
    with pytest.raises(HTTPException) as exc:
        await auth_module.get_current_user("Bearer [REDACTED]")
    assert exc.value.status_code == 503


def test_parse_limits_unit_cases():
    assert _parse_limits({"scope": {"department": "Mining"}, "modules": ["/ppe"]}) == \
        ({"department": "Mining"}, ["/ppe"])
    assert _parse_limits({"scope": "  ", "modules": "nope"}) == (None, None)
    assert _parse_limits({"scope": "Projects"}) == ("Projects", None)
    assert _parse_limits(None) == (None, None)
    assert _parse_limits(["scope"]) == (None, None)
    # Non-string module entries are dropped, never stored.
    assert _parse_limits({"scope": None, "modules": ["/ppe", 42, None]}) == (None, ["/ppe"])


# ─── modules: the mount-loop gate ────────────────────────────────────────────

from app.module_access import MODULE_LIMIT_MESSAGE, ROUTER_MODULES, require_module_access


def _gate_user(modules):
    user = {"user_id": "u-x", "email": "x@y.com", "role": "user"}
    if modules != "absent":
        user["modules"] = modules
    return user


async def _check(monkeypatch, module, user):
    import app.module_access as ma

    async def fake_optional(authorization=None):
        return user

    monkeypatch.setattr(ma, "get_current_user_optional", fake_optional)
    await require_module_access(module)("Bearer [REDACTED]")


async def test_module_gate_shared_plumbing_always_passes(monkeypatch):
    await _check(monkeypatch, None, _gate_user([]))


async def test_module_gate_anonymous_passes_through_to_route_auth(monkeypatch):
    import app.module_access as ma

    async def fake_optional(authorization=None):
        return None

    monkeypatch.setattr(ma, "get_current_user_optional", fake_optional)
    await require_module_access("/maintenance")("Bearer [REDACTED]")


async def test_module_gate_account_without_modules_is_unchanged(monkeypatch):
    await _check(monkeypatch, "/maintenance", _gate_user("absent"))


async def test_module_gate_member_passes_own_modules(monkeypatch):
    await _check(monkeypatch, "/employees", _gate_user(["/employees", "/ppe"]))
    await _check(monkeypatch, "/ppe", _gate_user(["/employees", "/ppe"]))


async def test_module_gate_member_blocked_elsewhere_with_plain_sentence(monkeypatch):
    for path in ("/maintenance", "/api/maintenance", "/timesheets", "/tools", "/admin"):
        with pytest.raises(HTTPException) as exc:
            await _check(monkeypatch, path, _gate_user(["/employees", "/ppe"]))
        assert exc.value.status_code == 403
        assert exc.value.detail == MODULE_LIMIT_MESSAGE
        assert exc.value.detail == "This account can only use Employees and PPE."


async def test_module_gate_explicit_empty_list_denies_everything(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await _check(monkeypatch, "/employees", _gate_user([]))
    assert exc.value.status_code == 403


def test_every_loaded_router_is_mapped_to_a_module():
    from main import loaded_routers
    missing = [k for k in loaded_routers if k not in ROUTER_MODULES]
    assert missing == []
    for key, module in ROUTER_MODULES.items():
        assert module is None or (isinstance(module, str) and module.startswith("/")), key


def _mount_module_deps():
    """(mount key, [module hrefs]) for every router mount on the real app."""
    from main import app as full_app, loaded_routers
    by_router = {}
    for k, v in loaded_routers.items():
        if v is not None:
            by_router[id(v)] = k
    out = []
    for route in full_app.routes:
        if type(route).__name__ != "_IncludedRouter":
            continue
        ctx = route.include_context
        key = by_router.get(id(ctx.included_router), by_router.get(id(route.original_router)))
        mods = []
        for dep in ctx.dependencies or []:
            fn = getattr(dep, "dependency", None)
            if fn is not None and getattr(fn, "__qualname__", "") == \
                    "require_module_access.<locals>._check":
                closure = dict(zip(fn.__code__.co_freevars,
                                   [c.cell_contents for c in (fn.__closure__ or [])]))
                mods.append(closure.get("module"))
        out.append((key, mods))
    return out


def test_every_mount_carries_its_mapped_module_gate():
    from app.module_access import ROUTER_MODULES
    bad = []
    for key, mods in _mount_module_deps():
        if mods != [ROUTER_MODULES.get(key, f"/{key}")]:
            bad.append((key, mods))
    assert bad == []


def test_wired_modules_match_the_map_for_sample_routers():
    from app.module_access import ROUTER_MODULES
    seen = {k: m[0] for k, m in _mount_module_deps() if m}
    assert seen.get("employees") == "/employees"
    assert seen.get("maintenance") == "/maintenance"
    assert seen.get("ppe") == "/ppe"
    assert seen.get("tasks_events") == "/tasks-events"
    assert seen.get("signatures") is None
    assert seen.get("employees") == ROUTER_MODULES["employees"]


# ─── kept apart: department scope on real routers ────────────────────────────

def _person(base, pid, num, section, department, first, last):
    return {"id": pid, "employee_id": num, "section": section, "department": department,
            "first_name": first, "last_name": last, "designation": "Fitter",
            "position": "Fitter", "employment_type": "NEC", "archived": False,
            "date_of_engagement": "2020-01-01", "id_number": "ID%d" % pid}


def _fresh_store():
    return {
        "employees": [
            _person(None, 1, "C1001", "Mechanical", "Engineering", "Tendai", "Moyo"),
            _person(None, 2, "C1002", "Electrical", "", "Rudo", "Ncube"),
            _person(None, 3, "M0001", "Mining", "Mining", "Farai", " Moyo".strip()),
            _person(None, 4, "M0002", "Mining", "Mining", "Tinashe", "Sibanda"),
        ],
        "ppe_records": [
            {"id": 11, "employee_id": "C1001", "department": "Engineering",
             "ppe_type": "Worksuit", "status": "issued",
             "issue_date": "2026-01-01", "created_at": "2026-01-01T00:00:00"},
            {"id": 12, "employee_id": "C1002", "department": "",
             "ppe_type": "Boots", "status": "issued",
             "issue_date": "2026-02-01", "created_at": "2026-02-01T00:00:00"},
            {"id": 13, "employee_id": "M0001", "department": "Mining",
             "ppe_type": "Boots", "status": "issued",
             "issue_date": "2026-03-01", "created_at": "2026-03-01T00:00:00"},
        ],
        "ppe_order_list": [
            {"record_id": "1", "entry": {"employee_id": "C1001", "ppe_type": "Worksuit"}},
            {"record_id": "2", "entry": {"employee_id": "M0001", "ppe_type": "Boots"}},
        ],
        "breakdowns": [
            {"id": 21, "section": "Mechanical"},
            {"id": 22, "section": "Mining"},
        ],
    }


@pytest.fixture
def emp_client(monkeypatch):
    import app.routers.employees as mod
    return mount_router(monkeypatch, mod, "/api/employees", _fresh_store())


@pytest.fixture
def ppe_client(monkeypatch):
    import app.routers.ppe as mod
    return mount_router(monkeypatch, mod, "/api/ppe", _fresh_store())


@pytest.fixture
def order_client(monkeypatch):
    import app.routers.ppe_order_list as mod
    return mount_router(monkeypatch, mod, "/api/ppe-order-list", _fresh_store())


def _nums(resp):
    assert resp.status_code == 200, resp.text
    return sorted(r["employee_id"] for r in resp.json())


def test_mining_viewer_sees_only_mining_people(emp_client):
    emp_client.as_user(MINING)
    assert _nums(emp_client.get("/api/employees")) == ["M0001", "M0002"]


def test_unscoped_list_leaves_mining_out_blank_department_stays(emp_client):
    emp_client.as_user(ENG_MGR)
    assert _nums(emp_client.get("/api/employees")) == ["C1001", "C1002"]


def test_projects_viewer_still_sees_only_their_section(emp_client):
    emp_client.as_user(PROJECTS)
    assert emp_client.get("/api/employees").json() == []


def test_mining_viewer_404s_on_an_engineering_person(emp_client):
    emp_client.as_user(MINING)
    assert emp_client.get("/api/employees/1").status_code == 404
    assert emp_client.get("/api/employees/3").status_code == 200


def test_department_param_needs_a_manager(emp_client):
    emp_client.as_user(ENG_USER)
    assert emp_client.get("/api/employees?department=Mining").status_code == 403


def test_manager_department_param_sees_mining(emp_client):
    emp_client.as_user(ENG_MGR)
    assert _nums(emp_client.get("/api/employees?department=Mining")) == ["M0001", "M0002"]


def test_mining_search_stays_inside_mining(emp_client):
    emp_client.as_user(MINING)
    assert _nums(emp_client.get("/api/employees/search/Moyo")) == ["M0001"]


def test_unscoped_search_leaves_mining_out(emp_client):
    emp_client.as_user(ENG_MGR)
    assert _nums(emp_client.get("/api/employees/search/Moyo")) == ["C1001"]


def test_lists_agree_from_a_warm_cache(emp_client):
    # Unscoped caches; scoped bypasses the cache; the Mining slice has its own
    # key. Whatever the order, nobody ever sees the other slice's rows.
    emp_client.as_user(ENG_MGR)
    assert _nums(emp_client.get("/api/employees")) == ["C1001", "C1002"]
    emp_client.as_user(MINING)
    assert _nums(emp_client.get("/api/employees")) == ["M0001", "M0002"]
    emp_client.as_user(ENG_MGR)
    assert _nums(emp_client.get("/api/employees?department=Mining")) == ["M0001", "M0002"]
    assert _nums(emp_client.get("/api/employees")) == ["C1001", "C1002"]


def _ppe_ids(resp):
    assert resp.status_code == 200, resp.text
    return sorted(r["id"] for r in resp.json())


def test_ppe_list_kept_apart_both_ways(ppe_client):
    ppe_client.as_user(MINING)
    assert _ppe_ids(ppe_client.get("/api/ppe")) == [13]
    ppe_client.as_user(ENG_MGR)
    assert _ppe_ids(ppe_client.get("/api/ppe")) == [11, 12]
    ppe_client.as_user(ENG_MGR)
    assert _ppe_ids(ppe_client.get("/api/ppe?department=Mining")) == [13]
    ppe_client.as_user(ENG_USER)
    assert ppe_client.get("/api/ppe?department=Mining").status_code == 403


def test_ppe_single_engineering_record_404s_for_mining(ppe_client):
    ppe_client.as_user(MINING)
    assert ppe_client.get("/api/ppe/11").status_code == 404
    assert ppe_client.get("/api/ppe/13").status_code == 200


def test_ppe_stats_count_only_the_viewers_slice(ppe_client):
    ppe_client.as_user(MINING)
    body = ppe_client.get("/api/ppe/stats/summary").json()
    assert body["total_records"] == 1
    ppe_client.as_user(ENG_MGR)
    assert ppe_client.get("/api/ppe/stats/summary").json()["total_records"] == 2


def _order_ids(resp):
    assert resp.status_code == 200, resp.text
    return sorted(r["record_id"] for r in resp.json())


def test_order_list_kept_apart_and_manager_slice(order_client):
    order_client.as_user(MINING)
    assert _order_ids(order_client.get("/api/ppe-order-list")) == ["2"]
    order_client.as_user(ENG_MGR)
    assert _order_ids(order_client.get("/api/ppe-order-list")) == ["1"]
    order_client.as_user(ENG_MGR)
    assert _order_ids(order_client.get("/api/ppe-order-list?department=Mining")) == ["2"]
    order_client.as_user(ENG_USER)
    assert order_client.get("/api/ppe-order-list?department=Mining").status_code == 403


def test_order_patch_of_mining_line_404s_for_engineering(order_client):
    order_client.as_user(ENG_MGR)
    resp = order_client.patch("/api/ppe-order-list/2", json={"qty": 9})
    assert resp.status_code == 404
    resp = order_client.patch("/api/ppe-order-list/1", json={"qty": 9})
    assert resp.status_code == 200, resp.text


def test_order_remove_and_clear_touch_only_the_viewers_slice(order_client):
    order_client.as_user(ENG_MGR)
    assert order_client.post("/api/ppe-order-list/remove", json={"record_ids": ["2"]}).json() == {"ok": True}
    assert _order_ids(order_client.get("/api/ppe-order-list")) == ["1"]
    assert order_client.delete("/api/ppe-order-list").json() == {"ok": True}
    assert _order_ids(order_client.get("/api/ppe-order-list")) == []
    # The Mining line survived both operations.
    order_client.as_user(MINING)
    assert _order_ids(order_client.get("/api/ppe-order-list")) == ["2"]


def test_section_rule_tables_show_none_to_department_viewers():
    store = _fresh_store()
    db = FakeSupabase(store, [])
    query = db.table("breakdowns").select("*")
    import app.scope as scope_mod
    scoped = scope_mod.apply_list_scope(query, MINING, "breakdowns", db)
    assert scoped.execute().data == []
    assert scope_mod.row_visible(MINING, "breakdowns", {"section": "Mining"}, db) is False
    assert scope_mod.row_visible(MINING, "breakdowns", {"section": "Mechanical"}, db) is False
    # ... while section viewers still match exactly.
    assert scope_mod.row_visible({"role": "user", "scope": "Mechanical"},
                                 "breakdowns", {"section": "Mechanical"}, db) is True


# ─── admin: department + modules limits ───────────────────────────────────────

import app.routers.admin as admin_mod


class _AdminTable:
    def __init__(self, state):
        self._state = state
        self._pending = None

    def select(self, *a, **k):
        return self

    def update(self, data):
        self._pending = data
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        if self._pending is None:
            return type("R", (), {"data": [dict(self._state["profile"])]})()
        row = dict(self._state["profile"])
        row.update(self._pending)
        self._state["stored"] = dict(self._pending.get("permissions", {}))
        return type("R", (), {"data": [row]})()


class _AdminFake:
    def __init__(self, profile):
        self._state = {"profile": profile, "stored": None}

    def table(self, name):
        assert name == "user_profiles"
        return _AdminTable(self._state)


ADMIN_CALLER = {"user_id": "admin-1", "role": "admin", "scope": None}
TARGET = {"id": "u-min", "role": "user", "email": "miner@example.com"}


def _admin_client(monkeypatch, permissions):
    profile = dict(TARGET)
    profile["permissions"] = dict(permissions)
    fake = _AdminFake(profile)
    monkeypatch.setattr(admin_mod, "supabase", fake)
    return fake


async def test_admin_sets_department_canonical_and_modules(monkeypatch):
    fake = _admin_client(monkeypatch, {})
    result = await admin_mod.set_user_scope(
        "u-min", admin_mod.SetScopeRequest(department="mining", modules=["/employees", "/ppe"]),
        ADMIN_CALLER)
    assert fake._state["stored"]["scope"] == {"department": "Mining"}
    assert fake._state["stored"]["modules"] == ["/employees", "/ppe"]
    assert result["permissions"]["scope"] == {"department": "Mining"}


async def test_admin_rejects_unknown_department_and_module(monkeypatch):
    _admin_client(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        await admin_mod.set_user_scope(
            "u-min", admin_mod.SetScopeRequest(department="Mars"), ADMIN_CALLER)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await admin_mod.set_user_scope(
            "u-min", admin_mod.SetScopeRequest(modules=["/employees", "/nope"]), ADMIN_CALLER)
    assert exc.value.status_code == 400


async def test_admin_rejects_scope_and_department_together(monkeypatch):
    _admin_client(monkeypatch, {})
    with pytest.raises(HTTPException) as exc:
        await admin_mod.set_user_scope(
            "u-min", admin_mod.SetScopeRequest(scope="Projects", department="Mining"),
            ADMIN_CALLER)
    assert exc.value.status_code == 400


async def test_admin_clears_both_limits_with_nulls(monkeypatch):
    fake = _admin_client(monkeypatch, {"scope": {"department": "Mining"},
                                       "modules": ["/employees", "/ppe"]})
    await admin_mod.set_user_scope(
        "u-min", admin_mod.SetScopeRequest(scope=None, department=None, modules=None),
        ADMIN_CALLER)
    assert "scope" not in fake._state["stored"]
    assert "modules" not in fake._state["stored"]


async def test_admin_omitted_fields_leave_limits_alone(monkeypatch):
    fake = _admin_client(monkeypatch, {"scope": "Projects", "modules": ["/ppe"]})
    await admin_mod.set_user_scope("u-min", admin_mod.SetScopeRequest(), ADMIN_CALLER)
    assert fake._state["stored"] == {"scope": "Projects", "modules": ["/ppe"]}


async def test_admin_empty_modules_is_an_explicit_empty_limit(monkeypatch):
    fake = _admin_client(monkeypatch, {})
    await admin_mod.set_user_scope(
        "u-min", admin_mod.SetScopeRequest(modules=[]), ADMIN_CALLER)
    assert fake._state["stored"]["modules"] == []


# ─── budget: Engineering-only ────────────────────────────────────────────────

@pytest.fixture
def budget_client(monkeypatch):
    import app.routers.ppe_budget as mod
    return mount_router(monkeypatch, mod, "/api/ppe/budget", {"ppe_budgets": []})


def test_budget_read_and_save_refuse_department_viewers(budget_client):
    budget_client.as_user(MINING_MGR)
    assert budget_client.get("/api/ppe/budget/2027").status_code == 403
    resp = budget_client.put("/api/ppe/budget/2027", json={
        "settings": {"lines": [],
                     "confirmed": {"count": 0, "start_month": 1, "boilermakers": 0},
                     "provisional": {"count": 0, "start_month": 1, "boilermakers": 0}},
        "expected_updated_at": None})
    assert resp.status_code == 403


def test_budget_read_still_open_to_engineering(budget_client):
    budget_client.as_user(ENG_USER)
    resp = budget_client.get("/api/ppe/budget/2027")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["calendar"]["months"]) == 12
