# tests/test_tasks_events_router.py — the manager-only Events & Tasks board.
#
# The CRUD shape (list/create/update/delete semantics) is already exhaustively
# covered by test_crud_router.py against the shared CrudRouter base class — this
# file only tests what's new here: the whole-router manager+ gate (mirrors
# test_accounting_router.py's proven pattern for the identical shape) actually
# rejects an insufficient-role caller (403), not just an anonymous one (401,
# already covered by test_endpoint_auth.py).

from fastapi.testclient import TestClient


class _FakeAuthClient:
    def __init__(self, user):
        self._user = user

    def get_user(self, token):
        class R: pass
        r = R()
        r.user = self._user
        return r


class _FakeRpcResult:
    def __init__(self, data):
        self.data = data


class _FakeAuthSupabase:
    def __init__(self, user, role):
        self.auth = _FakeAuthClient(user)
        self._role = role

    def rpc(self, name, params):
        return self

    def execute(self):
        return _FakeRpcResult(self._role)


class _FakeAuthUser:
    def __init__(self, uid="u-1", email="a@b.com"):
        self.id = uid
        self.email = email


class _SelectResp:
    def __init__(self, rows):
        self.data = rows


class _FakeTable:
    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def execute(self): return _SelectResp([])


class _FakeSupabase:
    def table(self, name):
        return _FakeTable()


def test_insufficient_role_is_403_not_401(monkeypatch):
    from app import auth as auth_mod
    from main import app

    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role="user"))
    client = TestClient(app)
    resp = client.get("/api/tasks-events", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 403, f"expected 403 for insufficient role, got {resp.status_code}: {resp.text}"


def test_manager_role_passes_the_gate(monkeypatch):
    from app import auth as auth_mod
    import app.crud_router as crud_mod
    from main import app

    monkeypatch.setattr(auth_mod, "supabase", _FakeAuthSupabase(_FakeAuthUser(), role="manager"))
    monkeypatch.setattr(crud_mod, "supabase", _FakeSupabase())
    client = TestClient(app)
    resp = client.get("/api/tasks-events", headers={"Authorization": "Bearer faketoken"})
    assert resp.status_code == 200, f"expected 200 for manager role, got {resp.status_code}: {resp.text}"
