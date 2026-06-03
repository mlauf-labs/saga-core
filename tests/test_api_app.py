"""Unit tests for the FastAPI application factory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from docstore.api.app import create_app
from docstore.core.config import AppConfig


def test_health_endpoint() -> None:
    app = create_app(AppConfig())
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("enabled", [True, False])
def test_swagger_toggle(enabled: bool) -> None:
    cfg = AppConfig()
    cfg.api.enable_swagger = enabled
    app = create_app(cfg)
    client = TestClient(app)
    assert (client.get("/docs").status_code == 200) is enabled
