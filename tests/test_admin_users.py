# tests/test_admin_users.py — covers admin.py's handlers that
# test_admin_router.py doesn't: list_users, update_user in full, plus the
# error-path branches of invite_user/set_user_active/send_password_reset that
# test_admin_router.py's happy/authz-focused tests don't reach (invite's "no
# user returned" and the invite-succeeded-but-role-update-failed 207, the ban
# API raising, "no data returned after update", and each handler's generic
# except-Exception -> 500). Same "call the route coroutine directly against a
# fake supabase client" recipe and fakes as test_admin_router.py, extended
# with raise-on-demand hooks.

import pytest
from fastapi import HTTPException

import app.routers.admin as admin


# ─── Fakes (same shape as test_admin_router.py, extended with raise hooks) ──

class _SelectResp:
    def __init__(self, rows):
        self.data = rows


class _FakeTable:
    def __init__(self, name, state):
        self.name = name
        self.state = state
        self._pending_update = None

    def select(self, *a, **k):
        self.state["mode"] = "select"
        return self

    def update(self, data):
        self.state["mode"] = "update"
        self._pending_update = data
        self.state["last_update"] = (self.name, data)
        return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self.state["mode"] == "select" and self.state.get("select_raises"):
            raise Exception(self.state["select_raises"])
        if self.state["mode"] == "update" and self.state.get("update_raises"):
            raise Exception(self.state["update_raises"])
        if self.state["mode"] == "select":
            return _SelectResp(self.state["profiles"].get(self.name, []))
        if self.state.get("update_returns_empty"):
            return _SelectResp([])
        row = dict((self.state["profiles"].get(self.name) or [{}])[0])
        row.update(self._pending_update or {})
        return _SelectResp([row])


class _FakeAuthAdmin:
    def __init__(self, state):
        self.state = state

    def invite_user_by_email(self, email, options=None):
        self.state["invite_calls"].append((email, options))
        if self.state.get("invite_raises"):
            raise Exception(self.state["invite_raises"])
        class _R:
            pass
        r = _R()
        if self.state.get("invite_no_user"):
            r.user = None
        else:
            u = _R()
            u.id = self.state.get("new_user_id", "new-user-id")
            r.user = u
        return r

    def update_user_by_id(self, uid, attributes):
        self.state["ban_calls"].append((uid, attributes))
        if self.state.get("ban_raises"):
            raise Exception(self.state["ban_raises"])


class _FakeAuth:
    def __init__(self, state):
        self.admin = _FakeAuthAdmin(state)
        self.state = state

    def reset_password_for_email(self, email, options=None):
        self.state["reset_calls"].append((email, options))
        if self.state.get("reset_raises"):
            raise Exception(self.state["reset_raises"])


class _FakeSupabase:
    def __init__(self, state):
        self.state = state
        self.auth = _FakeAuth(state)

    def table(self, name):
        return _FakeTable(name, self.state)


def _patch(monkeypatch, profiles=None, **extra):
    state = {
        "profiles": profiles or {},
        "mode": None,
        "last_update": None,
        "invite_calls": [], "ban_calls": [], "reset_calls": [],
        **extra,
    }
    monkeypatch.setattr(admin, "supabase", _FakeSupabase(state))
    return state


def _profile(id="u-1", role="user", email="a@b.com"):
    return {"id": id, "role": role, "email": email}


# ─── list_users ───────────────────────────────────────────────────────────

async def test_list_users_returns_all_profiles(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-1"), _profile(id="u-2", role="admin")]})
    result = await admin.list_users(current_user={"role": "admin", "user_id": "u-9"})
    assert len(result) == 2
    assert {r["id"] for r in result} == {"u-1", "u-2"}


async def test_list_users_empty_returns_empty_list(monkeypatch):
    _patch(monkeypatch, profiles={})
    result = await admin.list_users(current_user={"role": "admin", "user_id": "u-9"})
    assert result == []


async def test_list_users_raises_500_on_error(monkeypatch):
    _patch(monkeypatch, select_raises="db down")
    with pytest.raises(HTTPException) as exc:
        await admin.list_users(current_user={"role": "admin", "user_id": "u-9"})
    assert exc.value.status_code == 500


# ─── update_user ──────────────────────────────────────────────────────────

async def test_update_user_404_when_target_missing(monkeypatch):
    _patch(monkeypatch, profiles={})
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("ghost", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 404


async def test_plain_admin_cannot_edit_super_admin_target(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="super_admin")]})
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


async def test_plain_admin_cannot_edit_own_role(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-1", role="admin")]})
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-1", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


async def test_plain_admin_cannot_promote_to_super_admin(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="user")]})
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="super_admin"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


async def test_update_user_rejects_invalid_role(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="user")]})
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="bogus"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 400


async def test_plain_admin_can_promote_ordinary_user_to_manager(monkeypatch):
    state = _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="user")]})
    result = await admin.update_user("u-2", admin.UserProfileUpdate(role="manager"),
                                      current_user={"role": "admin", "user_id": "u-1"})
    assert result["role"] == "manager"
    assert state["last_update"][0] == "user_profiles"
    assert state["last_update"][1]["role"] == "manager"


async def test_super_admin_can_edit_own_role(monkeypatch):
    state = _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-1", role="super_admin")]})
    result = await admin.update_user("u-1", admin.UserProfileUpdate(role="admin"),
                                      current_user={"role": "super_admin", "user_id": "u-1"})
    assert result["role"] == "admin"


async def test_update_user_lookup_error_raises_500(monkeypatch):
    _patch(monkeypatch, select_raises="db down")
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_update_user_500_when_update_fails(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="user")]}, update_raises="db down")
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_update_user_500_when_no_data_returned(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="user")]}, update_returns_empty=True)
    with pytest.raises(HTTPException) as exc:
        await admin.update_user("u-2", admin.UserProfileUpdate(role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


# ─── invite_user — error branches not covered by test_admin_router.py ─────

async def test_invite_no_user_returned_is_500(monkeypatch):
    _patch(monkeypatch, invite_no_user=True)
    with pytest.raises(HTTPException) as exc:
        await admin.invite_user(admin.InviteUserRequest(email="a@b.com"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_invite_succeeds_but_role_update_fails_is_207(monkeypatch):
    _patch(monkeypatch, update_raises="role update failed")
    with pytest.raises(HTTPException) as exc:
        await admin.invite_user(admin.InviteUserRequest(email="a@b.com", role="manager"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 207


# ─── set_user_active — error branches not covered by test_admin_router.py ──

async def test_set_active_ban_api_failure_is_500(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2")]}, ban_raises="auth service down")
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("u-2", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_set_active_500_when_no_data_returned(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2")]}, update_returns_empty=True)
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("u-2", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_set_active_500_when_table_update_raises(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2")]}, update_raises="db down")
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("u-2", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_set_active_blocked_for_super_admin_target(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="super_admin")]})
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("u-2", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


# ─── send_password_reset — error branch not covered by test_admin_router.py ─

async def test_reset_password_supabase_error_is_500(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", email="x@y.com")]}, reset_raises="mail server down")
    with pytest.raises(HTTPException) as exc:
        await admin.send_password_reset("u-2", current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


async def test_reset_password_404_when_target_missing(monkeypatch):
    _patch(monkeypatch, profiles={})
    with pytest.raises(HTTPException) as exc:
        await admin.send_password_reset("ghost", current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 404
