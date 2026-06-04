"""Unit tests for the LangChain chat-model factory (offline construction)."""

from __future__ import annotations

import pytest

from docstore.core.errors import ConfigError
from docstore.llm.config import LlmConfig, LlmProviderSettings
from docstore.llm.providers import build_chat_model, build_fallback_chat_model


def _config(
    provider: str, settings: LlmProviderSettings, fallback_model: str | None = None
) -> LlmConfig:
    return LlmConfig(
        provider=provider, providers={provider: settings}, fallback_model=fallback_model
    )


def test_build_ollama_chat_model() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b", base_url="http://x:11434"))
    model = build_chat_model(cfg)
    assert type(model).__name__ == "ChatOllama"


def test_build_openai_chat_model() -> None:
    cfg = _config("openai", LlmProviderSettings(model="gpt-4o-mini", api_key="sk-test"))
    model = build_chat_model(cfg)
    assert type(model).__name__ == "ChatOpenAI"


def test_build_azure_chat_model() -> None:
    cfg = _config(
        "azure",
        LlmProviderSettings(
            deployment="dep",
            api_key="k",
            endpoint="https://example.openai.azure.com",
            api_version="2024-10-21",
        ),
    )
    model = build_chat_model(cfg)
    assert type(model).__name__ == "AzureChatOpenAI"


def test_openai_requires_api_key() -> None:
    cfg = _config("openai", LlmProviderSettings(model="gpt-4o-mini"))
    with pytest.raises(ConfigError):
        build_chat_model(cfg)


def test_unknown_provider_raises() -> None:
    cfg = LlmConfig(provider="nope", providers={"nope": LlmProviderSettings()})
    with pytest.raises(ConfigError):
        build_chat_model(cfg)


def test_fallback_disabled_by_default() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b"))
    assert build_fallback_chat_model(cfg) is None


def test_fallback_built_when_configured() -> None:
    cfg = _config(
        "ollama",
        LlmProviderSettings(model="llama3.2:1b", base_url="http://x:11434"),
        fallback_model="llama3.1:8b",
    )
    fallback = build_fallback_chat_model(cfg)
    assert fallback is not None
    assert type(fallback).__name__ == "ChatOllama"
