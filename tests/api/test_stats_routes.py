from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from saga.api.app import create_app
from saga.api.dependencies import JobQueue, Services
from saga.core.config import AppConfig
from saga.core.models import ArchiveCounts
from saga.metrics.snapshot import Snapshot, StorageSnapshot


@pytest.fixture
def client() -> TestClient:
    cfg = AppConfig()
    cfg.security.bearer_tokens = "secret"
    services = Services(
        config=cfg,
        db=AsyncMock(),
        opensearch=AsyncMock(),
        minio=AsyncMock(),
        queue=cast(JobQueue, fakeredis.aioredis.FakeRedis()),
        search=AsyncMock(),
    )
    app = create_app(config=cfg, services=services)
    snap = Snapshot(
        counts=ArchiveCounts(documents_total=2),
        storage=StorageSnapshot(postgres_bytes=10),
    )
    # Set state directly — no lifespan runs when not used as context manager.
    app.state.services = services
    app.state.snapshot = AsyncMock()
    app.state.snapshot.collect = AsyncMock(return_value=snap)
    return TestClient(app)


def test_stats_requires_auth(client: TestClient) -> None:
    assert client.get("/stats").status_code == 401


def test_stats_returns_snapshot(client: TestClient) -> None:
    r = client.get("/stats", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.json()["snapshot"]["counts"]["documents_total"] == 2


def test_metrics_is_unauthed_and_prometheus(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "saga_documents_total" in r.text


def test_stats_includes_pipeline_aggregates(client: TestClient) -> None:
    r = client.get("/stats", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = r.json()
    assert "pipeline" in body
    assert "stages" in body["pipeline"]
