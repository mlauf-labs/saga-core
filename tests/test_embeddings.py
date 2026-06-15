"""Unit tests for embedding configuration and provider adapters (mocked SDKs)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from saga.core.errors import ConfigError, ProviderError
from saga.embeddings.config import (
    EmbeddingProviderSettings,
    EmbeddingsConfig,
    load_embeddings_config,
)
from saga.embeddings.providers import (
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


# ---------------------------------------------------------------------------
# Multi-server fleet (least-busy distribution + failover)
# ---------------------------------------------------------------------------


def _fleet(monkeypatch: pytest.MonkeyPatch, clients: dict[str, SimpleNamespace]) -> None:
    """Install a fake ollama module with one client per host and a 2-server fleet."""
    from saga.ollama.config import OllamaRuntimeConfig, OllamaServerConfig

    class _FakeModule:
        AsyncClient = staticmethod(lambda host=None, **k: clients[host])

        class ResponseError(Exception):
            def __init__(self, message: str, status_code: int = 500) -> None:
                super().__init__(message)
                self.status_code = status_code

    monkeypatch.setitem(sys.modules, "ollama", _FakeModule)
    runtime = OllamaRuntimeConfig(
        servers=[OllamaServerConfig(url=url) for url in clients],
    )
    monkeypatch.setattr("saga.ollama.load_ollama_runtime_config", lambda *a, **k: runtime)


async def test_ollama_embed_distributes_batches_across_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_a = AsyncMock(return_value=SimpleNamespace(embeddings=[[1.0]]))
    embed_b = AsyncMock(return_value=SimpleNamespace(embeddings=[[2.0]]))
    _fleet(
        monkeypatch,
        {
            "http://emb-dist-a:1": SimpleNamespace(embed=embed_a),
            "http://emb-dist-b:1": SimpleNamespace(embed=embed_b),
        },
    )
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"), batch_size=1)
    vectors = await provider.embed(["x", "y"])
    assert sorted(vectors) == [[1.0], [2.0]]
    assert embed_a.await_count == 1
    assert embed_b.await_count == 1


async def test_ollama_embed_fails_over_to_second_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    embed_a = AsyncMock(side_effect=httpx.ConnectError("switched off"))
    embed_b = AsyncMock(return_value=SimpleNamespace(embeddings=[[2.0]]))
    _fleet(
        monkeypatch,
        {
            "http://emb-fo-a:1": SimpleNamespace(embed=embed_a),
            "http://emb-fo-b:1": SimpleNamespace(embed=embed_b),
        },
    )
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"))
    vectors = await provider.embed(["x"])
    assert vectors == [[2.0]]
    snapshot = provider._pool.snapshot()
    healthy = {url: entry["healthy"] for url, entry in snapshot.items()}
    assert False in healthy.values() and True in healthy.values()


async def test_ollama_embed_all_servers_down_raises_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    _fleet(
        monkeypatch,
        {
            "http://emb-down-a:1": SimpleNamespace(
                embed=AsyncMock(side_effect=httpx.ConnectError("off"))
            ),
            "http://emb-down-b:1": SimpleNamespace(
                embed=AsyncMock(side_effect=httpx.ConnectError("off"))
            ),
        },
    )
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"))
    with pytest.raises(ProviderError):
        await provider.embed(["x"])


async def test_ollama_embed_client_error_is_not_masked_by_failover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embed_b = AsyncMock(return_value=SimpleNamespace(embeddings=[[2.0]]))
    clients = {
        "http://emb-400-a:1": SimpleNamespace(embed=None),
        "http://emb-400-b:1": SimpleNamespace(embed=embed_b),
    }
    _fleet(monkeypatch, clients)
    import ollama  # the fake module installed by _fleet

    bad_request = ollama.ResponseError("invalid input", status_code=400)
    clients["http://emb-400-a:1"].embed = AsyncMock(side_effect=bad_request)
    clients["http://emb-400-b:1"].embed = AsyncMock(side_effect=bad_request)
    provider = OllamaEmbeddings(EmbeddingProviderSettings(model="m"))
    with pytest.raises(ProviderError):
        await provider.embed(["x"])
    # A 400 is our own bug: no failover, exactly one server was asked.
    total_calls = sum(
        client.embed.await_count for client in clients.values() if client.embed is not None
    )
    assert total_calls == 1


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
