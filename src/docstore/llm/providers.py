"""Concrete LLM provider adapters and a factory (FR-33 / NFR-34).

Supported providers: ``ollama`` (default), ``openai``, ``azure``. Selection and
model/endpoint settings come from ``providers.yaml``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docstore.core.errors import ConfigError, ProviderError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from docstore.llm.base import LlmProvider
    from docstore.llm.config import LlmConfig, LlmProviderSettings

_log = get_logger("docstore.llm")

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _require(value: str | None, field: str, provider: str) -> str:
    if not value:
        raise ConfigError(
            f"LLM provider '{provider}' requires '{field}' to be set in providers.yaml."
        )
    return value


class OllamaLlm:
    """LLM adapter backed by a local/remote Ollama instance."""

    name = "ollama"

    def __init__(self, settings: LlmProviderSettings) -> None:
        from ollama import AsyncClient

        self._model = _require(settings.model, "model", self.name)
        self._temperature = settings.temperature
        self._num_predict = settings.max_output_tokens
        self._client = AsyncClient(host=settings.base_url, timeout=settings.request_timeout)

    async def complete(self, *, prompt: str, json_mode: bool = True) -> str:
        try:
            response = await self._client.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                format="json" if json_mode else "",
                options={"temperature": self._temperature, "num_predict": self._num_predict},
            )
        except Exception as exc:
            raise ProviderError(f"Ollama chat request failed: {exc}") from exc
        content = response.message.content
        if not content:
            raise ProviderError("Ollama returned an empty response.")
        return str(content)

    async def aclose(self) -> None:
        return None


class _OpenAICompatibleLlm:
    """Shared implementation for the OpenAI and Azure OpenAI adapters."""

    name = "openai"

    def __init__(
        self, model: str, temperature: float, max_output_tokens: int, client: object
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client = client

    async def complete(self, *, prompt: str, json_mode: bool = True) -> str:
        kwargs: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self._client.chat.completions.create(**kwargs)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ProviderError(f"{self.name} chat request failed: {exc}") from exc
        content = response.choices[0].message.content
        if not content:
            raise ProviderError(f"{self.name} returned an empty response.")
        return str(content)

    async def aclose(self) -> None:
        await self._client.close()  # type: ignore[attr-defined]


class OpenAILlm(_OpenAICompatibleLlm):
    """LLM adapter for the OpenAI API."""

    name = "openai"

    def __init__(self, settings: LlmProviderSettings) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=_require(settings.api_key, "api_key", self.name),
            base_url=settings.base_url or _DEFAULT_OPENAI_BASE_URL,
            timeout=settings.request_timeout,
        )
        super().__init__(
            model=_require(settings.model, "model", self.name),
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            client=client,
        )


class AzureLlm(_OpenAICompatibleLlm):
    """LLM adapter for Azure OpenAI (model = deployment name)."""

    name = "azure"

    def __init__(self, settings: LlmProviderSettings) -> None:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            api_key=_require(settings.api_key, "api_key", self.name),
            azure_endpoint=_require(settings.endpoint, "endpoint", self.name),
            api_version=_require(settings.api_version, "api_version", self.name),
            timeout=settings.request_timeout,
        )
        super().__init__(
            model=_require(settings.deployment, "deployment", self.name),
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            client=client,
        )


_PROVIDERS: dict[str, Callable[[LlmProviderSettings], LlmProvider]] = {
    "ollama": OllamaLlm,
    "openai": OpenAILlm,
    "azure": AzureLlm,
}


def build_llm_provider(config: LlmConfig) -> LlmProvider:
    """Construct the configured LLM provider adapter (FR-33)."""
    factory = _PROVIDERS.get(config.provider)
    if factory is None:
        raise ConfigError(
            f"Unknown LLM provider '{config.provider}'. Supported: {sorted(_PROVIDERS)}."
        )
    provider = factory(config.active)
    _log.info("llm_provider_ready", provider=config.provider)
    return provider
