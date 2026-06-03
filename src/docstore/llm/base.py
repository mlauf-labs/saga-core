"""LLM provider interface (Ollama / OpenAI / Azure), NFR-34."""

from __future__ import annotations

from typing import Protocol


class LlmProvider(Protocol):
    """Minimal chat/completion interface used for document analysis."""

    name: str

    async def complete(self, *, prompt: str, json_mode: bool = True) -> str:
        """Return the model's text response for ``prompt``.

        When ``json_mode`` is true the provider instructs the model to return a
        single JSON object. Raises :class:`docstore.core.errors.ProviderError` on
        failure.
        """
        ...

    async def aclose(self) -> None:
        """Release any underlying network resources."""
        ...
