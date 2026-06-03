"""Unit tests for embedding configuration and provider adapters (mocked SDKs)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docstore.core.errors import ConfigError, ProviderError
from docstore.embeddings.config import (
    EmbeddingProviderSettings,
    EmbeddingsConfig,
    load_embeddings_config,
)
from docstore.embeddings.providers import (
    OllamaEmbeddings,
    OpenAIEmbeddings,
    build_embedding_provider,
)


def test_load_embeddings_config_from_repo() -> None:
    config = load_embeddings_config("config")
    assert config.provider == "ollama"
    assert config.active.dimension == 768


def test_active_missing_provider_raises() -> None:
    config = EmbeddingsConfig(provider="missing", providers={})
    with pytest.raises(ConfigError):
        _ = config.active


async def test_ollama_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = SimpleNamespace(
        embed=AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2], [0.3, 0.4]]))
    )

    class _FakeModule:
        AsyncClient = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(sys.modules, "ollama", _FakeModule)
    provider = OllamaEmbeddings(
        EmbeddingProviderSettings(model="nomic-embed-text", dimension=2), batch_size=10
    )
    vectors = await provider.embed(["a", "b"])
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    await provider.aclose()


async def test_ollama_embed_empty_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeModule:
        AsyncClient = lambda *a, **k: SimpleNamespace(embed=AsyncMock())  # noqa: E731

    monkeypatch.setitem(sys.modules, "ollama", _FakeModule)
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"))
    assert await provider.embed([]) == []


async def test_ollama_embed_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = SimpleNamespace(embed=AsyncMock(side_effect=RuntimeError("down")))

    class _FakeModule:
        AsyncClient = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(sys.modules, "ollama", _FakeModule)
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"))
    with pytest.raises(ProviderError):
        await provider.embed(["x"])


async def test_ollama_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    embed = AsyncMock(
        side_effect=[
            SimpleNamespace(embeddings=[[1.0], [2.0]]),
            SimpleNamespace(embeddings=[[3.0]]),
        ]
    )

    class _FakeModule:
        AsyncClient = lambda *a, **k: SimpleNamespace(embed=embed)  # noqa: E731

    monkeypatch.setitem(sys.modules, "ollama", _FakeModule)
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"), batch_size=2)
    vectors = await provider.embed(["a", "b", "c"])
    assert vectors == [[1.0], [2.0], [3.0]]
    assert embed.await_count == 2


async def test_openai_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[0.5, 0.6])])
    fake_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(return_value=response)),
        close=AsyncMock(),
    )

    class _FakeModule:
        AsyncOpenAI = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(sys.modules, "openai", _FakeModule)
    provider = OpenAIEmbeddings(
        EmbeddingProviderSettings(model="text-embedding-3-small", api_key="k", dimension=2)
    )
    vectors = await provider.embed(["hello"])
    assert vectors == [[0.5, 0.6]]
    await provider.aclose()
    fake_client.close.assert_awaited_once()


def test_build_embedding_provider_unknown() -> None:
    config = EmbeddingsConfig(provider="nope", providers={"nope": EmbeddingProviderSettings()})
    with pytest.raises(ConfigError):
        build_embedding_provider(config)
