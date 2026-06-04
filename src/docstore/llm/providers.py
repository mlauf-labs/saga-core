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

from typing import TYPE_CHECKING

from pydantic import SecretStr

from docstore.core.errors import ConfigError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from docstore.llm.config import LlmConfig, LlmProviderSettings

_log = get_logger("docstore.llm")

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _require(value: str | None, field: str, provider: str) -> str:
    if not value:
        raise ConfigError(
            f"LLM provider '{provider}' requires '{field}' to be set in providers.yaml."
        )
    return value


def _build_ollama(settings: LlmProviderSettings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    base_url = (settings.base_url or "http://ollama:11434").rstrip("/")
    return ChatOpenAI(
        model=_require(settings.model, "model", "ollama"),
        base_url=f"{base_url}/v1",
        # Ollama ignores the key, but the OpenAI client requires a non-empty value.
        api_key=SecretStr(settings.api_key or "ollama"),
        temperature=settings.temperature,
        max_completion_tokens=settings.max_output_tokens,
        timeout=settings.request_timeout,
        # The structured-output library already retries network errors; disable the
        # client's own retries so timeouts don't multiply across layers.
        max_retries=0,
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
