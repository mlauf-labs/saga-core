"""Unit tests for the startup model check (fake ollama SDK, no network)."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from saga.embeddings.config import EmbeddingProviderSettings, EmbeddingsConfig
from saga.llm.config import (
    LlmConfig,
    LlmProviderSettings,
    PipelineStepModelConfig,
    PipelineStepsConfig,
)
from saga.ollama.config import OllamaRuntimeConfig, OllamaServerConfig
from saga.ollama.health import (
    collect_required_ollama_models,
    ensure_ollama_models,
    resolve_check_urls,
)


class _FakeResponseError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakePullStream:
    def __init__(self, error: Exception | None = None) -> None:
        self._parts = [SimpleNamespace(status="pulling", completed=1, total=2)]
        self._error = error

    def __aiter__(self) -> _FakePullStream:
        return self

    async def __anext__(self) -> Any:
        if self._parts:
            return self._parts.pop(0)
        if self._error is not None:
            error, self._error = self._error, None
            raise error
        raise StopAsyncIteration


class _FakeClient:
    def __init__(
        self,
        models: list[str] | Exception,
        pull_error: Exception | None = None,
    ) -> None:
        self._models = models
        self._pull_error = pull_error
        self.pulled: list[str] = []
        self._client = SimpleNamespace(aclose=_noop)

    async def list(self) -> SimpleNamespace:
        if isinstance(self._models, Exception):
            raise self._models
        return SimpleNamespace(models=[SimpleNamespace(model=m) for m in self._models])

    async def pull(self, model: str, stream: bool = True) -> _FakePullStream:
        self.pulled.append(model)
        return _FakePullStream(self._pull_error)


async def _noop() -> None:
    return None


def _patch_ollama(
    monkeypatch: pytest.MonkeyPatch, clients: dict[str, _FakeClient]
) -> None:
    module = SimpleNamespace(
        AsyncClient=lambda host, timeout=None: clients[host],
        ResponseError=_FakeResponseError,
    )
    monkeypatch.setitem(sys.modules, "ollama", module)


def _llm_config(**kwargs: Any) -> LlmConfig:
    return LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="llama3.1:8b")},
        **kwargs,
    )


def _emb_config(provider: str = "ollama") -> EmbeddingsConfig:
    return EmbeddingsConfig(
        provider=provider,
        providers={provider: EmbeddingProviderSettings(model="nomic-embed-text")},
    )


# ---------------------------------------------------------------------------
# collect_required_ollama_models
# ---------------------------------------------------------------------------


def test_collect_includes_all_configured_models() -> None:
    llm = _llm_config(
        fallback_model="llama3.2:1b",
        steps=PipelineStepsConfig(
            doc_type=PipelineStepModelConfig(model="qwen2.5:7b", fallback_model="tiny"),
            summary=PipelineStepModelConfig(model="llama3.1:8b"),  # duplicate of global
        ),
    )
    models = collect_required_ollama_models(llm, _emb_config())
    assert models == {"llama3.1:8b", "llama3.2:1b", "qwen2.5:7b", "tiny", "nomic-embed-text"}


def test_collect_skips_non_ollama_sections() -> None:
    llm = LlmConfig(
        provider="openai",
        providers={"openai": LlmProviderSettings(model="gpt-4o-mini", api_key="k")},
    )
    assert collect_required_ollama_models(llm, _emb_config()) == {"nomic-embed-text"}
    assert collect_required_ollama_models(_llm_config(), _emb_config("openai")) == {"llama3.1:8b"}
    assert collect_required_ollama_models(None, None) == set()


# ---------------------------------------------------------------------------
# resolve_check_urls
# ---------------------------------------------------------------------------


def test_resolve_check_urls_uses_fleet_and_dedups() -> None:
    runtime = OllamaRuntimeConfig(
        servers=[OllamaServerConfig(url="http://a:1"), OllamaServerConfig(url="http://b:1")]
    )
    urls = resolve_check_urls(_llm_config(), _emb_config(), runtime)
    assert urls == ("http://a:1", "http://b:1")


def test_resolve_check_urls_falls_back_to_base_url() -> None:
    llm = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="m", base_url="http://solo:11434")},
    )
    urls = resolve_check_urls(llm, _emb_config("openai"), OllamaRuntimeConfig())
    assert urls == ("http://solo:11434",)


def test_resolve_check_urls_empty_when_no_ollama_provider() -> None:
    llm = LlmConfig(provider="openai", providers={"openai": LlmProviderSettings(model="m")})
    assert resolve_check_urls(llm, _emb_config("openai"), OllamaRuntimeConfig()) == ()


# ---------------------------------------------------------------------------
# ensure_ollama_models
# ---------------------------------------------------------------------------


async def test_missing_model_is_pulled(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(models=["llama3.1:8b"])
    _patch_ollama(monkeypatch, {"http://a:1": client})

    result = await ensure_ollama_models(
        ["http://a:1"], {"llama3.1:8b", "nomic-embed-text"}, pull_missing=True
    )

    assert client.pulled == ["nomic-embed-text"]
    assert result == {"http://a:1": []}


async def test_latest_tag_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(models=["nomic-embed-text:latest", "llama3.1:8b"])
    _patch_ollama(monkeypatch, {"http://a:1": client})

    result = await ensure_ollama_models(
        ["http://a:1"], {"nomic-embed-text", "llama3.1:8b"}, pull_missing=True
    )

    assert client.pulled == []
    assert result == {"http://a:1": []}


async def test_check_only_reports_missing_without_pulling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(models=[])
    _patch_ollama(monkeypatch, {"http://a:1": client})

    result = await ensure_ollama_models(["http://a:1"], {"m1"}, pull_missing=False)

    assert client.pulled == []
    assert result == {"http://a:1": ["m1"]}


async def test_unreachable_server_never_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = _FakeClient(models=httpx.ConnectError("switched off"))
    up = _FakeClient(models=["m1"])
    _patch_ollama(monkeypatch, {"http://down:1": down, "http://up:1": up})

    result = await ensure_ollama_models(["http://down:1", "http://up:1"], {"m1"})

    assert result == {"http://down:1": ["m1"], "http://up:1": []}


async def test_failed_pull_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(models=[], pull_error=_FakeResponseError("no such model", 404))
    _patch_ollama(monkeypatch, {"http://a:1": client})

    result = await ensure_ollama_models(["http://a:1"], {"typo-model"}, pull_missing=True)

    assert client.pulled == ["typo-model"]
    assert result == {"http://a:1": ["typo-model"]}


async def test_no_models_or_urls_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await ensure_ollama_models([], {"m"}) == {}
    assert await ensure_ollama_models(["http://a:1"], set()) == {"http://a:1": []}
