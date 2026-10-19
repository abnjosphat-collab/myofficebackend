# tests/_documents_client.py — a small app with the two document routers (registered in the same order as main.py), signed in
# as a chosen role, over the in-memory database from _documents_fake.py. Going through the real HTTP layer means the tests
# also cover route matching, validation and status codes, not just the handler bodies.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import audit, auth
import app.routers.document_folders as folders_mod
import app.routers.documents as documents_mod

ORIGINAL_GET_CURRENT_USER = auth.get_current_user  # the very function the routers' Depends(...) hold

USER_ID = "00000000-0000-0000-0000-0000000000a1"


def make_client(monkeypatch, db, role: str = "manager", name: str = "Edson Mavhondo") -> TestClient:
    user = {"user_id": USER_ID, "email": "edson@example.com", "role": role, "name": name}

    async def who(authorization=None):
        return user

    monkeypatch.setattr(auth, "get_current_user", who)  # what require_role(...) calls
    monkeypatch.setattr(documents_mod, "supabase", db)
    monkeypatch.setattr(folders_mod, "supabase", db)
    audit._COLUMN_PROBE.clear()

    app = FastAPI()
    app.include_router(documents_mod.router)
    app.include_router(folders_mod.router)
    app.dependency_overrides[ORIGINAL_GET_CURRENT_USER] = who
    return TestClient(app)


def U(n: int) -> str:
    """A valid uuid for test row number n (the routers refuse ids that are not uuids)."""
    return f"00000000-0000-0000-0000-{n:012d}"
