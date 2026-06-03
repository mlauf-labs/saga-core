"""Unit tests for ARQ queue/worker wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from docstore.core.config import RedisConfig
from docstore.pipeline import worker as worker_module
from docstore.pipeline.queue import INGEST_JOB, redis_settings


def test_redis_settings_from_dsn() -> None:
    settings = redis_settings(RedisConfig(url="redis://example:6380/2"))
    assert settings.host == "example"
    assert settings.port == 6380
    assert settings.database == 2


def test_ingest_job_constant() -> None:
    assert INGEST_JOB == "ingest_document"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCSTORE_API_TOKENS", "t")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "p")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("MINIO_SECRET_KEY", "s")


def test_configure_populates_redis_settings() -> None:
    settings = worker_module.configure()
    assert settings.redis_settings is not None


async def test_on_startup_bootstraps_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrapped: list[str] = []

    async def fake_os_bootstrap(self: Any) -> None:
        bootstrapped.append("opensearch")

    async def fake_minio_bootstrap(self: Any) -> None:
        bootstrapped.append("minio")

    monkeypatch.setattr("docstore.storage.OpenSearchStore.bootstrap", fake_os_bootstrap)
    monkeypatch.setattr("docstore.storage.MinioStore.bootstrap", fake_minio_bootstrap)

    ctx: dict[str, Any] = {}
    await worker_module.on_startup(ctx)
    assert set(bootstrapped) == {"opensearch", "minio"}
    assert "config" in ctx and "opensearch" in ctx and "minio" in ctx


async def test_on_shutdown_closes_opensearch() -> None:
    closer = AsyncMock()
    ctx = {"opensearch": type("S", (), {"close": closer})()}
    await worker_module.on_shutdown(ctx)
    closer.assert_awaited_once()
