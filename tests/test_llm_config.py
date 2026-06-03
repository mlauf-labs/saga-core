"""Unit tests for LLM provider configuration loading."""

from __future__ import annotations

import pytest

from docstore.core.errors import ConfigError
from docstore.llm.config import LlmConfig, LlmProviderSettings, load_llm_config


def test_load_llm_config_from_repo() -> None:
    config = load_llm_config("config")
    assert config.provider == "ollama"
    assert "ollama" in config.providers
    assert config.active.model is not None


def test_active_missing_provider_raises() -> None:
    config = LlmConfig(provider="missing", providers={})
    with pytest.raises(ConfigError):
        _ = config.active


def test_active_returns_selected() -> None:
    config = LlmConfig(
        provider="openai",
        providers={
            "ollama": LlmProviderSettings(model="llama"),
            "openai": LlmProviderSettings(model="gpt", api_key="k"),
        },
    )
    assert config.active.model == "gpt"
