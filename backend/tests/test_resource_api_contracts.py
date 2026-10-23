from app.main import app


def test_mvp_resource_routes_are_mounted_with_shared_api_schemas() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/batches" in paths
    assert "post" in paths["/api/v1/batches"]
    assert "get" in paths["/api/v1/batches"]
    assert "/demands" in paths
    assert "/demands/{demand_id}" in paths
    assert "/shelters/{shelter_id}/events" in paths

    batch_post = paths["/api/v1/batches"]["post"]
    assert batch_post["responses"]["201"]
    assert "Idempotency-Key" in {
        parameter["name"] for parameter in batch_post["parameters"]
    }
    assert "HTTPBearer" in schema["components"]["securitySchemes"]


def test_public_liveness_route_does_not_require_authentication() -> None:
    schema = app.openapi()
    assert "security" not in schema["paths"]["/health/live"]["get"]
