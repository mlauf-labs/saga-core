"""Embedding provider interface (Ollama / OpenAI / Azure), NFR-34."""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Produces embedding vectors for text."""

    name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text.

        Raises :class:`docstore.core.errors.ProviderError` on failure.
        """
        ...

    async def aclose(self) -> None:
        """Release any underlying network resources."""
        ...
