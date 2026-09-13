from fastapi.testclient import TestClient

from tests.support.api_factory import build_test_settings
from ticketing.core.config import get_settings
from ticketing.main import create_app

pytestmark = __import__("pytest").mark.integration


def test_metrics_expose_http_and_real_database_duration_without_route_cardinality() -> None:
    settings = build_test_settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        missing = client.get(
            "/random-resource-id-that-must-not-be-a-metric-label",
            headers={"X-Correlation-ID": "missing-route-correlation-123"},
        )
        metrics = client.get("/metrics")

    assert ready.status_code == 200
    assert missing.status_code == 404
    assert missing.headers["X-Correlation-ID"] == "missing-route-correlation-123"
    assert missing.json()["detail"]["code"] == "http_404"
    assert missing.json()["correlation_id"] == "missing-route-correlation-123"
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")
    assert (
        'ticketing_http_requests_total{method="GET",route="/health/ready",status="200"} 1.0'
        in metrics.text
    )
    assert "ticketing_http_request_duration_seconds_bucket" in metrics.text
    assert 'ticketing_database_query_duration_seconds_count{operation="SELECT"}' in metrics.text
    assert (
        'ticketing_http_requests_total{method="GET",route="unmatched",status="404"} 1.0'
        in metrics.text
    )
    assert "random-resource-id-that-must-not-be-a-metric-label" not in metrics.text
