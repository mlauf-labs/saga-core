"""Unit tests for LLM provider adapters and the factory (mocked SDK clients)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from docstore.core.errors import ConfigError, ProviderError
from docstore.llm.config import LlmConfig, LlmProviderSettings
from docstore.llm.providers import OllamaLlm, OpenAILlm, build_llm_provider


def _ollama(monkeypatch: pytest.MonkeyPatch, response: object) -> OllamaLlm:
    fake_client = SimpleNamespace(chat=AsyncMock(return_value=response))

    class _FakeModule:
        AsyncClient = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(__import__("sys").modules, "ollama", _FakeModule)
    return OllamaLlm(LlmProviderSettings(model="llama3.1", base_url="http://x:11434"))


async def test_ollama_complete_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))
    provider = _ollama(monkeypatch, response)
    assert await provider.complete(prompt="hi") == '{"ok": true}'
    await provider.aclose()


async def test_ollama_empty_response_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(message=SimpleNamespace(content=""))
    provider = _ollama(monkeypatch, response)
    with pytest.raises(ProviderError):
        await provider.complete(prompt="hi")


async def test_ollama_request_error_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = SimpleNamespace(chat=AsyncMock(side_effect=RuntimeError("down")))

    class _FakeModule:
        AsyncClient = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(__import__("sys").modules, "ollama", _FakeModule)
    provider = OllamaLlm(LlmProviderSettings(model="llama3.1"))
    with pytest.raises(ProviderError, match="Ollama chat request failed"):
        await provider.complete(prompt="hi")


def test_ollama_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeModule:
        AsyncClient = lambda *a, **k: SimpleNamespace()  # noqa: E731

    monkeypatch.setitem(__import__("sys").modules, "ollama", _FakeModule)
    with pytest.raises(ConfigError):
        OllamaLlm(LlmProviderSettings())


async def test_openai_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"x": 1}'))]
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))
        ),
        close=AsyncMock(),
    )

    class _FakeModule:
        AsyncOpenAI = lambda *a, **k: fake_client  # noqa: E731

    monkeypatch.setitem(__import__("sys").modules, "openai", _FakeModule)
    provider = OpenAILlm(LlmProviderSettings(model="gpt-4o-mini", api_key="k"))
    assert await provider.complete(prompt="hi") == '{"x": 1}'
    await provider.aclose()
    fake_client.close.assert_awaited_once()


def test_openai_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeModule:
        AsyncOpenAI = lambda *a, **k: SimpleNamespace()  # noqa: E731

    monkeypatch.setitem(__import__("sys").modules, "openai", _FakeModule)
    with pytest.raises(ConfigError):
        OpenAILlm(LlmProviderSettings(model="gpt"))


def test_build_llm_provider_unknown() -> None:
    config = LlmConfig(provider="nope", providers={"nope": LlmProviderSettings()})
    with pytest.raises(ConfigError):
        build_llm_provider(config)
