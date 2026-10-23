# tests/test_job_cards_crud.py — job_cards' hand-written PATCH (the generic
# CrudRouter PATCH plus the F-09 optimistic-concurrency guard): a stale save is
# 409, a matching save writes without persisting the expectation, empty stays 400.
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import FakeSupabase
from app.auth import get_current_user as real_get_current_user
import app.crud_router as cr_mod
import app.routers.job_cards as mod


def _client(monkeypatch, rows):
    store = {"queries": [], "data": [dict(r) for r in rows]}
    fake = FakeSupabase(store)
    monkeypatch.setattr(mod, "supabase", fake)
    monkeypatch.setattr(cr_mod, "supabase", fake)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/job-cards")
    app.dependency_overrides[real_get_current_user] = lambda: {"user_id": "u1", "role": "manager", "email": "t@t"}
    return TestClient(app), store


def test_update_job_card_stale_expected_updated_at_is_409(monkeypatch):
    client, _store = _client(monkeypatch, [{"id": 1, "status": "open", "updated_at": "2026-08-10T12:00:00"}])
    r = client.patch("/api/job-cards/1", json={"status": "in_progress", "expected_updated_at": "2026-08-01T00:00:00"})
    assert r.status_code == 409


def test_update_job_card_matching_expectation_saves_without_storing_it(monkeypatch):
    client, store = _client(monkeypatch, [{"id": 1, "status": "open", "updated_at": "2026-08-10T12:00:00"}])
    r = client.patch("/api/job-cards/1", json={"status": "in_progress", "expected_updated_at": "2026-08-10T12:00:00"})
    assert r.status_code == 200
    assert store["update"]["status"] == "in_progress"
    assert store["update"]["updated_at"] != "2026-08-10T12:00:00"  # touch() restamps on save
    assert "expected_updated_at" not in store["update"]


def test_update_job_card_empty_patch_stays_400(monkeypatch):
    client, _store = _client(monkeypatch, [{"id": 1, "status": "open", "updated_at": "2026-08-10T12:00:00"}])
    r = client.patch("/api/job-cards/1", json={})
    assert r.status_code == 400


def test_update_job_card_missing_row_stays_404(monkeypatch):
    client, _store = _client(monkeypatch, [])
    r = client.patch("/api/job-cards/9", json={"status": "in_progress"})
    assert r.status_code == 404


def test_update_job_card_bad_id_stays_422(monkeypatch):
    client, _store = _client(monkeypatch, [{"id": 1, "status": "open"}])
    r = client.patch("/api/job-cards/nope", json={"status": "in_progress"})
    assert r.status_code == 422
