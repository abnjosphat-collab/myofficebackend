# tests/test_exception_handler.py — the global catch-all exception handler in main.py:
# an unexpected (non-HTTPException) error must return a generic 500 with NO internal
# error string or stack trace leaked to the client. Boots the full app via TestClient;
# no Redis/DB needed (the test route raises before any of that).

from fastapi.testclient import TestClient

from main import app

# A throwaway route that raises an unexpected error, to exercise the catch-all handler.
# Registered on the real app (harmless obscure path); TestClient must be told not to
# re-raise, since Starlette's ServerErrorMiddleware re-raises after producing the 500.
@app.get("/api/__test_unhandled_boom")
async def _boom():
    raise ValueError("super secret internal detail that must not leak")


client = TestClient(app, raise_server_exceptions=False)


def test_unhandled_exception_returns_generic_500():
    r = client.get("/api/__test_unhandled_boom")
    assert r.status_code == 500
    body = r.json()
    assert body == {"detail": "An unexpected error occurred. Please try again."}
    # The raw internal message must never appear in the client-facing response.
    assert "super secret internal detail" not in r.text


def test_normal_route_unaffected():
    # A sanity check that the handler doesn't swallow ordinary successful responses.
    r = client.get("/api/health")
    assert r.status_code == 200
