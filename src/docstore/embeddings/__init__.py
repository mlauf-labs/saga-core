"""Embedding provider abstraction (Ollama / OpenAI / Azure). Phase 4/5."""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Produces embedding vectors for text (NFR-34)."""

    name: str
    dimension: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""
        ...
