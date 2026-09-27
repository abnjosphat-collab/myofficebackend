from main import app


def test_pre_prefixed_routers_use_the_public_api_paths():
    paths = set(app.openapi()["paths"])

    assert "/api/training/reports/compliance_rate" in paths
    assert "/api/inventory/items" in paths
    assert "/api/reports/analytics/summary" in paths
    assert not any("/api/training/api/training" in path for path in paths)
    assert not any("/api/inventory/api/inventory" in path for path in paths)
    assert not any("/api/reports/api/reports" in path for path in paths)
