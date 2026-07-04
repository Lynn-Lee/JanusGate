"""Prometheus metrics endpoint regression tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_metrics_endpoint_exposes_prometheus_request_metrics() -> None:
    with TestClient(app) as client:
        health_response = client.get("/health")
        metrics_response = client.get("/metrics")

    assert health_response.status_code == 200
    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith("text/plain")

    body = metrics_response.text
    assert "# HELP janusgate_http_requests_total" in body
    assert "# TYPE janusgate_http_requests_total counter" in body
    assert (
        'janusgate_http_requests_total{method="GET",path="/health",status_code="200"}'
        in body
    )
    assert "# HELP janusgate_http_request_duration_seconds" in body
    assert 'janusgate_http_request_duration_seconds_bucket{method="GET",path="/health",' in body
