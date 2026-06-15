"""Unit tests for the FastAPI application factory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from saga.api.app import create_app
from saga.api.dependencies import Services


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("enabled", [True, False])
def test_swagger_toggle(services: Services, enabled: bool) -> None:
    services.config.api.enable_swagger = enabled
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert (client.get("/docs").status_code == 200) is enabled
