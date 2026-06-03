"""Concrete embedding provider adapters and a factory (FR-33 / NFR-34).

Supported providers: ``ollama`` (default), ``openai``, ``azure``. Texts are sent in
configurable batches; vectors are returned in input order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docstore.core.errors import ConfigError, ProviderError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from docstore.embeddings.base import EmbeddingProvider
    from docstore.embeddings.config import EmbeddingProviderSettings, EmbeddingsConfig

_log = get_logger("docstore.embeddings")

_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _require(value: str | None, field: str, provider: str) -> str:
    if not value:
        raise ConfigError(f"Embedding provider '{provider}' requires '{field}' in providers.yaml.")
    return value


def _batched(texts: list[str], size: int) -> list[list[str]]:
    step = max(size, 1)
    return [texts[i : i + step] for i in range(0, len(texts), step)]


class OllamaEmbeddings:
    """Embedding adapter backed by an Ollama instance."""

    name = "ollama"

    def __init__(self, settings: EmbeddingProviderSettings, *, batch_size: int = 32) -> None:
        from ollama import AsyncClient

        self._model = _require(settings.model, "model", self.name)
        self.dimension = settings.dimension
        self._batch_size = batch_size
        self._client = AsyncClient(host=settings.base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        try:
            for batch in _batched(texts, self._batch_size):
                response = await self._client.embed(model=self._model, input=batch)
                vectors.extend([list(map(float, vec)) for vec in response.embeddings])
        except Exception as exc:
            raise ProviderError(f"Ollama embedding request failed: {exc}") from exc
        return vectors

    async def aclose(self) -> None:
        return None


class _OpenAICompatibleEmbeddings:
    """Shared implementation for the OpenAI and Azure OpenAI embedding adapters."""

    name = "openai"

    def __init__(self, model: str, dimension: int, batch_size: int, client: object) -> None:
        self._model = model
        self.dimension = dimension
        self._batch_size = batch_size
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        try:
            for batch in _batched(texts, self._batch_size):
                response = await self._client.embeddings.create(  # type: ignore[attr-defined]
                    model=self._model, input=batch
                )
                vectors.extend([list(item.embedding) for item in response.data])
        except Exception as exc:
            raise ProviderError(f"{self.name} embedding request failed: {exc}") from exc
        return vectors

    async def aclose(self) -> None:
        await self._client.close()  # type: ignore[attr-defined]


class OpenAIEmbeddings(_OpenAICompatibleEmbeddings):
    """Embedding adapter for the OpenAI API."""

    name = "openai"

    def __init__(self, settings: EmbeddingProviderSettings, *, batch_size: int = 32) -> None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=_require(settings.api_key, "api_key", self.name),
            base_url=settings.base_url or _DEFAULT_OPENAI_BASE_URL,
        )
        super().__init__(
            model=_require(settings.model, "model", self.name),
            dimension=settings.dimension,
            batch_size=batch_size,
            client=client,
        )


class AzureEmbeddings(_OpenAICompatibleEmbeddings):
    """Embedding adapter for Azure OpenAI (model = deployment name)."""

    name = "azure"

    def __init__(self, settings: EmbeddingProviderSettings, *, batch_size: int = 32) -> None:
        from openai import AsyncAzureOpenAI

        client = AsyncAzureOpenAI(
            api_key=_require(settings.api_key, "api_key", self.name),
            azure_endpoint=_require(settings.endpoint, "endpoint", self.name),
            api_version=_require(settings.api_version, "api_version", self.name),
        )
        super().__init__(
            model=_require(settings.deployment, "deployment", self.name),
            dimension=settings.dimension,
            batch_size=batch_size,
            client=client,
        )


_PROVIDERS: dict[str, Callable[[EmbeddingProviderSettings, int], EmbeddingProvider]] = {
    "ollama": lambda s, b: OllamaEmbeddings(s, batch_size=b),
    "openai": lambda s, b: OpenAIEmbeddings(s, batch_size=b),
    "azure": lambda s, b: AzureEmbeddings(s, batch_size=b),
}


def build_embedding_provider(config: EmbeddingsConfig) -> EmbeddingProvider:
    """Construct the configured embedding provider adapter (FR-33)."""
    factory = _PROVIDERS.get(config.provider)
    if factory is None:
        raise ConfigError(
            f"Unknown embedding provider '{config.provider}'. Supported: {sorted(_PROVIDERS)}."
        )
    provider = factory(config.active, config.batch_size)
    _log.info("embedding_provider_ready", provider=config.provider, dimension=provider.dimension)
    return provider
