"""Unit tests for LLM provider configuration loading."""

from __future__ import annotations

import pytest

from saga.core.errors import ConfigError
from saga.llm.config import (
    LlmConfig,
    LlmProviderSettings,
    PipelineStepModelConfig,
    PipelineStepsConfig,
    load_llm_config,
)


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


def test_fallback_disabled_by_default() -> None:
    config = LlmConfig(provider="ollama", providers={"ollama": LlmProviderSettings(model="m")})
    assert config.fallback is None


def test_fallback_copies_active_with_new_model() -> None:
    config = LlmConfig(
        provider="openai",
        providers={"openai": LlmProviderSettings(model="gpt-4o-mini", api_key="k")},
        fallback_model="gpt-4o",
    )
    fallback = config.fallback
    assert fallback is not None
    assert fallback.model == "gpt-4o"
    assert fallback.api_key == "k"  # inherited from the active provider


# ---------------------------------------------------------------------------
# Per-step model overrides
# ---------------------------------------------------------------------------


def _config_with_steps(
    *,
    doc_type_model: str | None = None,
    summary_fallback: str | None = None,
) -> LlmConfig:
    return LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="llama3.1:8b")},
        steps=PipelineStepsConfig(
            doc_type=PipelineStepModelConfig(model=doc_type_model),
            summary=PipelineStepModelConfig(fallback_model=summary_fallback),
        ),
    )


def test_step_active_returns_global_when_no_override() -> None:
    config = _config_with_steps()
    settings = config.step_active("value_extraction")
    assert settings.model == "llama3.1:8b"
    assert settings is config.active


def test_step_active_overrides_model_for_configured_step() -> None:
    config = _config_with_steps(doc_type_model="qwen2.5:7b")
    settings = config.step_active("doc_type")
    assert settings.model == "qwen2.5:7b"


def test_step_active_inherits_connection_settings() -> None:
    """The step override must only change the model name, not other settings."""
    config = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="base", base_url="http://x:11434")},
        steps=PipelineStepsConfig(folder_placement=PipelineStepModelConfig(model="big-model")),
    )
    step = config.step_active("folder_placement")
    assert step.model == "big-model"
    assert step.base_url == "http://x:11434"


def test_step_fallback_returns_none_when_nothing_configured() -> None:
    config = _config_with_steps()
    assert config.step_fallback("doc_type") is None


def test_step_fallback_uses_global_fallback_when_no_step_override() -> None:
    config = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="llama3.1:8b")},
        fallback_model="llama3.2:1b",
    )
    fallback = config.step_fallback("summary")
    assert fallback is not None
    assert fallback.model == "llama3.2:1b"


def test_step_fallback_uses_step_level_override() -> None:
    config = _config_with_steps(summary_fallback="llama3.2:1b")
    fallback = config.step_fallback("summary")
    assert fallback is not None
    assert fallback.model == "llama3.2:1b"


def test_step_fallback_step_override_wins_over_global() -> None:
    """Step-level fallback_model takes priority over the global fallback_model."""
    config = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="llama3.1:8b")},
        fallback_model="global-fallback",
        steps=PipelineStepsConfig(doc_type=PipelineStepModelConfig(fallback_model="step-fallback")),
    )
    assert config.step_fallback("doc_type").model == "step-fallback"  # type: ignore[union-attr]
    assert config.step_fallback("summary").model == "global-fallback"  # type: ignore[union-attr]


def test_has_step_model_override_false_when_unset() -> None:
    config = _config_with_steps()
    assert config.has_step_model_override("doc_type") is False


def test_has_step_model_override_true_when_model_set() -> None:
    config = _config_with_steps(doc_type_model="qwen")
    assert config.has_step_model_override("doc_type") is True


def test_has_step_model_override_true_when_fallback_set() -> None:
    config = _config_with_steps(summary_fallback="tiny")
    assert config.has_step_model_override("summary") is True


def test_step_active_empty_string_treated_as_no_override() -> None:
    """An empty string from an unset env var must not override the global model."""
    config = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="llama3.1:8b")},
        steps=PipelineStepsConfig(value_extraction=PipelineStepModelConfig(model="")),
    )
    assert config.step_active("value_extraction").model == "llama3.1:8b"


def test_load_llm_config_from_repo_includes_steps() -> None:
    """providers.yaml must deserialise with the steps section present."""
    config = load_llm_config("config")
    # All four steps should exist with no overrides (env vars empty by default).
    assert config.steps.doc_type.model in (None, "")
    assert config.steps.folder_placement.model in (None, "")
