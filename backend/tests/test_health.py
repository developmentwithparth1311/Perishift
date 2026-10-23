import json

from app import main


def test_health_endpoints_report_liveness_and_readiness(monkeypatch) -> None:
    live = main.live()
    assert live == {"status": "ok"}

    monkeypatch.setattr(main, "database_is_ready", lambda: True)
    ready = main.ready()
    assert ready.status_code == 200
    assert json.loads(ready.body) == {
        "status": "ok",
        "checks": {"database": "ok"},
    }

    monkeypatch.setattr(main, "database_is_ready", lambda: False)
    unavailable = main.ready()
    assert unavailable.status_code == 503
    assert json.loads(unavailable.body) == {
        "status": "unavailable",
        "checks": {"database": "unavailable"},
    }

    paths = main.app.openapi()["paths"]
    assert "/health/live" in paths
    assert "/health/ready" in paths
