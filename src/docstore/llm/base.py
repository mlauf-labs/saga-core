"""LLM provider interface (Ollama / OpenAI / Azure). Adapters in Phase 4 (NFR-34)."""

from __future__ import annotations

from typing import Protocol


class LlmProvider(Protocol):
    """Minimal chat/completion interface used for document analysis."""

    name: str

    async def complete(self, *, prompt: str, json_mode: bool = True) -> str:
        """Return the model's text response for ``prompt``."""
        ...
