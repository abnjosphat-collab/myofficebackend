# tests/test_admin_router.py — the admin panel's invite/deactivate/reset-password
# endpoints (app/routers/admin.py). Unit-level: call the route functions directly
# (bypassing FastAPI's Depends resolution, same style as test_accounting_router.py),
# with a fake Supabase client covering both .table(...) and .auth/.auth.admin.

import pytest
from fastapi import HTTPException
import app.routers.admin as admin


# ─── Fakes ──────────────────────────────────────────────────────────────────

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

    def execute(self):
        if self.state["mode"] == "select":
            return _SelectResp(self.state["profiles"].get(self.name, []))
        # update — return the row merged with whatever was set, echoing real Supabase's
        # "returns the updated row" behavior.
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


# ─── _assert_may_act_on (pure logic) ───────────────────────────────────────

def test_plain_admin_cannot_act_on_self():
    with pytest.raises(HTTPException) as exc:
        admin._assert_may_act_on("admin", "u-1", _profile(id="u-1"), "deactivate")
    assert exc.value.status_code == 403


def test_plain_admin_cannot_act_on_super_admin():
    with pytest.raises(HTTPException) as exc:
        admin._assert_may_act_on("admin", "u-2", _profile(id="u-1", role="super_admin"), "deactivate")
    assert exc.value.status_code == 403


def test_plain_admin_can_act_on_ordinary_user():
    admin._assert_may_act_on("admin", "u-2", _profile(id="u-1", role="user"), "deactivate")  # no raise


def test_super_admin_can_act_on_anyone_including_self():
    admin._assert_may_act_on("super_admin", "u-1", _profile(id="u-1", role="super_admin"), "deactivate")


# ─── invite_user ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invite_rejects_invalid_role(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await admin.invite_user(admin.InviteUserRequest(email="a@b.com", role="bogus"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_plain_admin_cannot_invite_as_admin(monkeypatch):
    _patch(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await admin.invite_user(admin.InviteUserRequest(email="a@b.com", role="admin"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_invite_as_admin(monkeypatch):
    state = _patch(monkeypatch)
    result = await admin.invite_user(admin.InviteUserRequest(email="a@b.com", role="admin"),
                                      current_user={"role": "super_admin", "user_id": "u-1"})
    assert result["role"] == "admin"
    assert state["invite_calls"] == [("a@b.com", {"redirect_to": f"{admin.FRONTEND_URL}/auth/callback"})]
    # non-default role -> a follow-up role update must have been issued
    assert state["last_update"] == ("user_profiles", {"role": "admin"})


@pytest.mark.asyncio
async def test_invite_default_role_skips_role_update_call(monkeypatch):
    state = _patch(monkeypatch)
    await admin.invite_user(admin.InviteUserRequest(email="a@b.com"),  # role defaults to "user"
                             current_user={"role": "admin", "user_id": "u-1"})
    assert state["last_update"] is None  # trigger already sets role='user' — no PATCH needed


@pytest.mark.asyncio
async def test_invite_surfaces_supabase_error(monkeypatch):
    _patch(monkeypatch, invite_raises="mailbox rejected")
    with pytest.raises(HTTPException) as exc:
        await admin.invite_user(admin.InviteUserRequest(email="a@b.com"),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 500


# ─── set_user_active ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_bans_and_updates_is_active(monkeypatch):
    state = _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2")]})
    result = await admin.set_user_active("u-2", admin.SetActiveRequest(active=False),
                                          current_user={"role": "admin", "user_id": "u-1"})
    assert state["ban_calls"] == [("u-2", {"ban_duration": admin.BAN_FOREVER})]
    assert result["is_active"] is False


@pytest.mark.asyncio
async def test_reactivate_unbans(monkeypatch):
    state = _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2")]})
    await admin.set_user_active("u-2", admin.SetActiveRequest(active=True),
                                 current_user={"role": "admin", "user_id": "u-1"})
    assert state["ban_calls"] == [("u-2", {"ban_duration": "none"})]


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-1")]})
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("u-1", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_deactivate_404_when_user_missing(monkeypatch):
    _patch(monkeypatch, profiles={})
    with pytest.raises(HTTPException) as exc:
        await admin.set_user_active("ghost", admin.SetActiveRequest(active=False),
                                     current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 404


# ─── send_password_reset ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reset_password_sends_to_targets_email(monkeypatch):
    state = _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", email="target@x.com")]})
    result = await admin.send_password_reset("u-2", current_user={"role": "admin", "user_id": "u-1"})
    assert result == {"ok": True}
    assert state["reset_calls"] == [("target@x.com", {"redirect_to": f"{admin.FRONTEND_URL}/auth/callback"})]


@pytest.mark.asyncio
async def test_reset_password_blocked_for_super_admin_target(monkeypatch):
    _patch(monkeypatch, profiles={"user_profiles": [_profile(id="u-2", role="super_admin")]})
    with pytest.raises(HTTPException) as exc:
        await admin.send_password_reset("u-2", current_user={"role": "admin", "user_id": "u-1"})
    assert exc.value.status_code == 403
