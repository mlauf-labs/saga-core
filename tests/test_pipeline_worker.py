"""Unit tests for ARQ queue/worker wiring (hermetic: no real sockets).

``on_startup`` builds the storage adapters lazily (clients are only created on first
use), so the only network-touching calls are the three ``bootstrap()`` coroutines and
the LLM/embedding builders. These are monkeypatched so the worker can wire its context
without any real connections.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from saga.core.config import RedisConfig
from saga.pipeline import worker as worker_module
from saga.pipeline.queue import INGEST_JOB, redis_settings


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "t")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "p")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("MINIO_SECRET_KEY", "s")


def test_redis_settings_from_dsn() -> None:
    settings = redis_settings(RedisConfig(url="redis://example:6380/2"))
    assert settings.host == "example"
    assert settings.port == 6380
    assert settings.database == 2


def test_ingest_job_constant() -> None:
    assert INGEST_JOB == "ingest_document"


def test_configure_populates_redis_settings() -> None:
    settings = worker_module.configure()
    assert settings.redis_settings is not None


class _FakeEmbedder:
    dimension = 8

    async def aclose(self) -> None:  # pragma: no cover - not exercised here
        return None


async def test_on_startup_wires_context_without_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrapped: list[str] = []

    async def fake_pg_bootstrap(self: Any) -> None:
        bootstrapped.append("postgres")

    async def fake_os_bootstrap(self: Any) -> None:
        bootstrapped.append("opensearch")

    async def fake_minio_bootstrap(self: Any) -> None:
        bootstrapped.append("minio")

    monkeypatch.setattr("saga.storage.PostgresStore.bootstrap", fake_pg_bootstrap)
    monkeypatch.setattr("saga.storage.OpenSearchStore.bootstrap", fake_os_bootstrap)
    monkeypatch.setattr("saga.storage.MinioStore.bootstrap", fake_minio_bootstrap)
    # Avoid constructing real LLM/embedding clients (no network, no provider config).
    monkeypatch.setattr(worker_module, "build_chat_model", lambda cfg: object())
    monkeypatch.setattr(worker_module, "build_fallback_chat_model", lambda cfg: None)
    monkeypatch.setattr(worker_module, "build_embedding_provider", lambda cfg: _FakeEmbedder())
    monkeypatch.setattr(worker_module, "ensure_ollama_models", AsyncMock(return_value={}))

    ctx: dict[str, Any] = {}
    await worker_module.on_startup(ctx)

    assert set(bootstrapped) == {"postgres", "opensearch", "minio"}
    for key in (
        "config",
        "llm_config",
        "db",
        "opensearch",
        "minio",
        "converters",
        "analyzer",
        "chunker",
        "embedder",
    ):
        assert key in ctx, f"missing ctx key: {key}"


def _patch_hermetic_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_bootstrap(self: Any) -> None:
        return None

    monkeypatch.setattr("saga.storage.PostgresStore.bootstrap", _noop_bootstrap)
    monkeypatch.setattr("saga.storage.OpenSearchStore.bootstrap", _noop_bootstrap)
    monkeypatch.setattr("saga.storage.MinioStore.bootstrap", _noop_bootstrap)
    monkeypatch.setattr(worker_module, "build_chat_model", lambda cfg: object())
    monkeypatch.setattr(worker_module, "build_fallback_chat_model", lambda cfg: None)
    monkeypatch.setattr(worker_module, "build_embedding_provider", lambda cfg: _FakeEmbedder())


async def test_on_startup_checks_ollama_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_hermetic_startup(monkeypatch)
    ensure = AsyncMock(return_value={})
    monkeypatch.setattr(worker_module, "ensure_ollama_models", ensure)

    await worker_module.on_startup({})

    # Default config: LLM + embeddings provider are ollama -> blocking check runs.
    ensure.assert_awaited_once()
    urls = ensure.await_args.args[0]
    models = ensure.await_args.args[1]
    assert urls and all(url.startswith("http") for url in urls)
    assert models  # at least the LLM + embedding models from providers.yaml


async def test_on_startup_skips_check_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from saga.ollama.config import OllamaRuntimeConfig

    _patch_hermetic_startup(monkeypatch)
    ensure = AsyncMock(return_value={})
    monkeypatch.setattr(worker_module, "ensure_ollama_models", ensure)
    monkeypatch.setattr(
        worker_module,
        "load_ollama_runtime_config",
        lambda *a, **k: OllamaRuntimeConfig(startup_model_check=False),
    )

    await worker_module.on_startup({})

    ensure.assert_not_awaited()


async def test_on_shutdown_closes_resources() -> None:
    os_close = AsyncMock()
    db_close = AsyncMock()
    conv_close = AsyncMock()
    emb_close = AsyncMock()
    ctx = {
        "opensearch": type("OS", (), {"close": os_close})(),
        "db": type("DB", (), {"close": db_close})(),
        "converters": type("CV", (), {"aclose": conv_close})(),
        "embedder": type("EM", (), {"aclose": emb_close})(),
    }

    await worker_module.on_shutdown(ctx)

    os_close.assert_awaited_once()
    db_close.assert_awaited_once()
    conv_close.assert_awaited_once()
    emb_close.assert_awaited_once()


async def test_on_shutdown_tolerates_empty_context() -> None:
    await worker_module.on_shutdown({})
