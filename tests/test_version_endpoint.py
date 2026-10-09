# tests/test_version_endpoint.py — GET /api/version reports the commit Render deployed (RENDER_GIT_COMMIT/BRANCH), so a
# deploy can be confirmed from outside, and reports null rather than a guess when those variables are absent.

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_reports_the_deployed_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "3fdd5737a1b2c3d4e5f60718293a4b5c6d7e8f90")
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["commit"] == "3fdd5737a1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert body["short_commit"] == "3fdd573"
    assert body["branch"] == "main"
    assert body["started_at"]


def test_unknown_outside_render(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("RENDER_GIT_BRANCH", raising=False)
    body = client.get("/api/version").json()
    assert body["commit"] is None and body["short_commit"] is None and body["branch"] is None


def test_needs_no_sign_in(monkeypatch):
    # Public on purpose, like /api/health: a commit id is not sensitive, and checking a deploy must not need an account.
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    assert client.get("/api/version").status_code == 200


def test_listed_on_the_root_endpoint():
    assert client.get("/").json()["endpoints"]["version"] == "/api/version"
