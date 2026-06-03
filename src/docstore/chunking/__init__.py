"""Chunking: Markdown-aware splitting with token-based fallback (FR-6). Phase 5."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docstore.core.config import ChunkingConfig


class MarkdownChunker:
    """Splits Markdown by structure, then by tokens when chunks are too long.

    The maximum chunk size (``max_tokens``) and overlap are configurable (FR-6).
    """

    def __init__(self, config: ChunkingConfig) -> None:
        self._config = config

    def split(self, markdown: str) -> list[str]:
        """Return a list of chunk texts for ``markdown``."""
        raise NotImplementedError("Chunking is implemented in Phase 5.")


__all__ = ["MarkdownChunker"]
