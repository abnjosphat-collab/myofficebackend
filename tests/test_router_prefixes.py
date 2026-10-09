from main import app


def test_pre_prefixed_routers_use_the_public_api_paths():
    paths = set(app.openapi()["paths"])

    assert "/api/training/reports/compliance_rate" in paths
    assert "/api/inventory/items" in paths
    assert not any("/api/training/api/training" in path for path in paths)
    assert not any("/api/inventory/api/inventory" in path for path in paths)
    # The mock /api/reports router (invented figures, no callers) was removed on 2026-10-09.
    assert not any(path.startswith("/api/reports") for path in paths)
