"""LangChain chat-model factory for structured extraction (FR-33 / NFR-34).

The structured-output library drives the LLM via tool calling, so we build a
LangChain ``BaseChatModel`` (Ollama / OpenAI / Azure) from ``providers.yaml`` rather
than calling the raw SDKs directly. Selection, models and endpoints are configurable.

Ollama is accessed through its **OpenAI-compatible** ``/v1`` endpoint via
``ChatOpenAI``: the structured-output library issues OpenAI-style tool calls
(``strict`` / ``parallel_tool_calls``) that the native ``ChatOllama`` binding does not
accept, whereas Ollama's OpenAI-compatible API does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from saga.core.errors import ConfigError
from saga.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from saga.llm.config import LlmConfig, LlmProviderSettings

_log = get_logger("saga.llm")

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _require(value: str | None, field: str, provider: str) -> str:
    if not value:
        raise ConfigError(
            f"LLM provider '{provider}' requires '{field}' to be set in providers.yaml."
        )
    return value


def _multi_server_clients(settings: LlmProviderSettings) -> dict[str, Any]:
    """httpx clients routing through the Ollama server fleet, when one is configured.

    With a single server (the default) this returns no kwargs and ``ChatOpenAI``
    behaves exactly as before. With multiple servers, requests are distributed
    least-busy across the fleet with automatic failover; the clients live for
    the process lifetime, like the openai SDK's implicitly created ones.
    """
    import httpx

    from saga.ollama import (
        OllamaFailoverAsyncTransport,
        OllamaFailoverTransport,
        get_pool,
        load_ollama_runtime_config,
    )

    runtime = load_ollama_runtime_config()
    servers = runtime.resolve_servers(settings.base_url)
    if len(servers) <= 1:
        return {}
    pool = get_pool(servers, cooldown_seconds=runtime.cooldown_seconds)
    return {
        "http_async_client": httpx.AsyncClient(
            transport=OllamaFailoverAsyncTransport(
                pool, connect_timeout=runtime.connect_timeout
            )
        ),
        "http_client": httpx.Client(
            transport=OllamaFailoverTransport(pool, connect_timeout=runtime.connect_timeout)
        ),
    }


def _build_ollama(settings: LlmProviderSettings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    base_url = (settings.base_url or "http://ollama:11434").rstrip("/")
    return ChatOpenAI(
        model=_require(settings.model, "model", "ollama"),
        # With multiple servers the base_url only contributes path/headers; the
        # failover transport rewrites scheme/host/port per request.
        base_url=f"{base_url}/v1",
        # Ollama ignores the key, but the OpenAI client requires a non-empty value.
        api_key=SecretStr(settings.api_key or "ollama"),
        temperature=settings.temperature,
        max_completion_tokens=settings.max_output_tokens,
        # With streaming=True this is the per-chunk read timeout, not the total
        # generation time; each received token resets the httpx read timer so
        # slow local models are not killed mid-generation.
        timeout=settings.request_timeout,
        streaming=settings.streaming,
        # The structured-output library already retries network errors; disable the
        # client's own retries so timeouts don't multiply across layers.
        max_retries=0,
        **_multi_server_clients(settings),
    )


def _build_openai(settings: LlmProviderSettings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=_require(settings.model, "model", "openai"),
        api_key=SecretStr(_require(settings.api_key, "api_key", "openai")),
        base_url=settings.base_url or _DEFAULT_OPENAI_BASE_URL,
        temperature=settings.temperature,
        max_completion_tokens=settings.max_output_tokens,
        timeout=settings.request_timeout,
        streaming=settings.streaming,
        max_retries=0,
    )


def _build_azure(settings: LlmProviderSettings) -> BaseChatModel:
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_deployment=_require(settings.deployment, "deployment", "azure"),
        api_key=SecretStr(_require(settings.api_key, "api_key", "azure")),
        azure_endpoint=_require(settings.endpoint, "endpoint", "azure"),
        api_version=_require(settings.api_version, "api_version", "azure"),
        temperature=settings.temperature,
        max_completion_tokens=settings.max_output_tokens,
        timeout=settings.request_timeout,
        streaming=settings.streaming,
        max_retries=0,
    )


_BUILDERS = {
    "ollama": _build_ollama,
    "openai": _build_openai,
    "azure": _build_azure,
}


def _build(provider: str, settings: LlmProviderSettings) -> BaseChatModel:
    builder = _BUILDERS.get(provider)
    if builder is None:
        raise ConfigError(f"Unknown LLM provider '{provider}'. Supported: {sorted(_BUILDERS)}.")
    return builder(settings)


def build_chat_model(config: LlmConfig) -> BaseChatModel:
    """Build the primary LangChain chat model for the configured provider (FR-33)."""
    model = _build(config.provider, config.active)
    _log.info("chat_model_ready", provider=config.provider, model=config.active.model)
    return model


def build_fallback_chat_model(config: LlmConfig) -> BaseChatModel | None:
    """Build the optional fallback chat model, or ``None`` when not configured."""
    fallback = config.fallback
    if fallback is None:
        return None
    model = _build(config.provider, fallback)
    _log.info("fallback_chat_model_ready", provider=config.provider, model=fallback.model)
    return model


def build_step_chat_models(config: LlmConfig) -> dict[str, BaseChatModel]:
    """Build per-step primary chat models for steps with an explicit model override.

    Steps without an override are omitted; the caller falls back to the global model
    for those steps.
    """
    from saga.llm.config import PIPELINE_STEPS

    result: dict[str, BaseChatModel] = {}
    for step in PIPELINE_STEPS:
        settings = config.step_active(step)
        if settings is config.active:
            continue
        result[step] = _build(config.provider, settings)
        _log.info(
            "step_chat_model_ready",
            step=step,
            provider=config.provider,
            model=settings.model,
        )
    return result


def build_step_fallback_chat_models(config: LlmConfig) -> dict[str, BaseChatModel]:
    """Build per-step fallback chat models for steps with an explicit fallback_model.

    Steps without a step-level fallback override are omitted; the caller falls back to
    the global fallback model for those steps.
    """
    from saga.llm.config import PIPELINE_STEPS

    result: dict[str, BaseChatModel] = {}
    for step in PIPELINE_STEPS:
        step_cfg = getattr(config.steps, step, None)
        if step_cfg is None or not step_cfg.fallback_model:
            continue
        settings = config.step_fallback(step)
        if settings is None:
            continue
        result[step] = _build(config.provider, settings)
        _log.info(
            "step_fallback_model_ready",
            step=step,
            provider=config.provider,
            model=settings.model,
        )
    return result
