"""Unit tests for the LangChain chat-model factory (offline construction)."""

from __future__ import annotations

import pytest

from saga.core.errors import ConfigError
from saga.llm.config import (
    LlmConfig,
    LlmProviderSettings,
    PipelineStepModelConfig,
    PipelineStepsConfig,
)
from saga.llm.providers import (
    build_chat_model,
    build_fallback_chat_model,
    build_step_chat_models,
    build_step_fallback_chat_models,
)


def _config(
    provider: str, settings: LlmProviderSettings, fallback_model: str | None = None
) -> LlmConfig:
    return LlmConfig(
        provider=provider, providers={provider: settings}, fallback_model=fallback_model
    )


def test_build_ollama_chat_model() -> None:
    # Ollama is routed through ChatOpenAI against its OpenAI-compatible /v1 endpoint.
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b", base_url="http://x:11434"))
    model = build_chat_model(cfg)
    assert type(model).__name__ == "ChatOpenAI"
    assert str(getattr(model, "openai_api_base", "")).rstrip("/").endswith("/v1")


def test_streaming_enabled_by_default() -> None:
    """streaming=True must be passed to the LangChain model (prevents idle-timeout kills)."""
    for provider, settings in [
        ("ollama", LlmProviderSettings(model="llama3.1:8b")),
        ("openai", LlmProviderSettings(model="gpt-4o-mini", api_key="sk-test")),
        (
            "azure",
            LlmProviderSettings(
                deployment="dep",
                api_key="k",
                endpoint="https://example.openai.azure.com",
                api_version="2024-10-21",
            ),
        ),
    ]:
        model = build_chat_model(_config(provider, settings))
        assert getattr(model, "streaming", None) is True, (
            f"streaming not enabled on {provider} model"
        )


def test_streaming_can_be_disabled_per_provider() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b", streaming=False))
    model = build_chat_model(cfg)
    assert getattr(model, "streaming", None) is False


def test_request_timeout_default_is_300() -> None:
    """Default timeout must be 300 s (5 minutes) to survive slow local models."""
    settings = LlmProviderSettings()
    assert settings.request_timeout == 300.0


def test_request_timeout_propagated_to_model() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b", request_timeout=600.0))
    model = build_chat_model(cfg)
    assert getattr(model, "request_timeout", None) == 600.0


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
    assert type(fallback).__name__ == "ChatOpenAI"


# ---------------------------------------------------------------------------
# Multi-server Ollama fleet
# ---------------------------------------------------------------------------


def _fleet(monkeypatch: pytest.MonkeyPatch, *urls: str) -> None:
    from saga.ollama.config import OllamaRuntimeConfig, OllamaServerConfig

    runtime = OllamaRuntimeConfig(servers=[OllamaServerConfig(url=url) for url in urls])
    monkeypatch.setattr("saga.ollama.load_ollama_runtime_config", lambda *a, **k: runtime)


def test_single_server_builds_plain_chat_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _fleet(monkeypatch, "http://llm-solo:11434")
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b"))
    model = build_chat_model(cfg)
    # Exactly the previous behaviour: no custom httpx clients, no pool.
    assert getattr(model, "http_async_client", None) is None
    assert getattr(model, "http_client", None) is None


def test_multi_server_injects_failover_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    from saga.ollama.transport import (
        OllamaFailoverAsyncTransport,
        OllamaFailoverTransport,
    )

    _fleet(monkeypatch, "http://llm-a:11434", "http://llm-b:11434")
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b"))
    model = build_chat_model(cfg)

    async_client = model.http_async_client
    sync_client = model.http_client
    assert isinstance(async_client._transport, OllamaFailoverAsyncTransport)
    assert isinstance(sync_client._transport, OllamaFailoverTransport)
    # Both directions share one pool, so load/health knowledge is shared.
    assert async_client._transport._pool is sync_client._transport._pool


def test_primary_and_fallback_share_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _fleet(monkeypatch, "http://llm-x:11434", "http://llm-y:11434")
    cfg = _config(
        "ollama",
        LlmProviderSettings(model="llama3.1:8b"),
        fallback_model="llama3.2:1b",
    )
    primary = build_chat_model(cfg)
    fallback = build_fallback_chat_model(cfg)
    assert fallback is not None
    assert primary.http_async_client._transport._pool is fallback.http_async_client._transport._pool


def test_multi_server_not_applied_to_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    _fleet(monkeypatch, "http://llm-a:11434", "http://llm-b:11434")
    cfg = _config("openai", LlmProviderSettings(model="gpt-4o-mini", api_key="sk-test"))
    model = build_chat_model(cfg)
    assert getattr(model, "http_async_client", None) is None


# ---------------------------------------------------------------------------
# Per-step factory functions
# ---------------------------------------------------------------------------


def _config_with_step_override(
    step: str, *, model: str, fallback_model: str | None = None
) -> LlmConfig:
    return LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="base-model", base_url="http://x:11434")},
        steps=PipelineStepsConfig(
            **{step: PipelineStepModelConfig(model=model, fallback_model=fallback_model)}
        ),
    )


def test_build_step_chat_models_empty_when_no_overrides() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b"))
    assert build_step_chat_models(cfg) == {}


def test_build_step_chat_models_builds_only_overridden_steps() -> None:
    cfg = _config_with_step_override("doc_type", model="qwen2.5:7b")
    step_models = build_step_chat_models(cfg)
    assert set(step_models.keys()) == {"doc_type"}
    assert type(step_models["doc_type"]).__name__ == "ChatOpenAI"


def test_build_step_chat_models_multiple_overrides() -> None:
    cfg = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="base", base_url="http://x:11434")},
        steps=PipelineStepsConfig(
            doc_type=PipelineStepModelConfig(model="fast"),
            folder_placement=PipelineStepModelConfig(model="large"),
        ),
    )
    step_models = build_step_chat_models(cfg)
    assert set(step_models.keys()) == {"doc_type", "folder_placement"}


def test_build_step_fallback_chat_models_empty_when_no_overrides() -> None:
    cfg = _config("ollama", LlmProviderSettings(model="llama3.1:8b"))
    assert build_step_fallback_chat_models(cfg) == {}


def test_build_step_fallback_chat_models_builds_configured_step() -> None:
    cfg = _config_with_step_override("summary", model="large", fallback_model="tiny")
    step_fallbacks = build_step_fallback_chat_models(cfg)
    assert set(step_fallbacks.keys()) == {"summary"}
    assert type(step_fallbacks["summary"]).__name__ == "ChatOpenAI"


def test_build_step_fallback_chat_models_ignores_steps_without_fallback() -> None:
    cfg = LlmConfig(
        provider="ollama",
        providers={"ollama": LlmProviderSettings(model="base", base_url="http://x:11434")},
        steps=PipelineStepsConfig(
            doc_type=PipelineStepModelConfig(model="override"),
            value_extraction=PipelineStepModelConfig(fallback_model="tiny"),
        ),
    )
    step_fallbacks = build_step_fallback_chat_models(cfg)
    # doc_type has no fallback_model; value_extraction has one
    assert set(step_fallbacks.keys()) == {"value_extraction"}
