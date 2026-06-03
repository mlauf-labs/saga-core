"""Chunking: Markdown-aware splitting with token-based fallback (FR-6).

A Markdown-header splitter preserves section boundaries; sections that exceed the
configured ``max_tokens`` are further split with a token-aware recursive splitter.
The token length function is injectable so tests run without downloading a tokenizer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from docstore.core.config import ChunkingConfig

# Header levels used to preserve document structure during splitting.
_HEADERS_TO_SPLIT_ON = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
]


def _tiktoken_length(encoding_name: str) -> Callable[[str], int]:
    """Return a length function that counts tokens, loading tiktoken lazily."""
    encoder: object | None = None

    def count(text: str) -> int:
        nonlocal encoder
        if encoder is None:
            import tiktoken

            encoder = tiktoken.get_encoding(encoding_name)
        return len(encoder.encode(text))  # type: ignore[attr-defined]

    return count


class MarkdownChunker:
    """Splits Markdown by structure, then by tokens when chunks are too long (FR-6)."""

    def __init__(
        self,
        config: ChunkingConfig,
        *,
        length_function: Callable[[str], int] | None = None,
    ) -> None:
        self._max_tokens = config.max_tokens
        self._length_function = length_function or _tiktoken_length(config.tokenizer)
        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=_HEADERS_TO_SPLIT_ON, strip_headers=False
        )
        self._token_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.max_tokens,
            chunk_overlap=config.overlap_tokens,
            length_function=self._length_function,
        )

    def split(self, markdown: str) -> list[str]:
        """Return a list of chunk texts for ``markdown`` (empty if blank)."""
        if not markdown.strip():
            return []

        chunks: list[str] = []
        for section in self._header_splitter.split_text(markdown):
            text = section.page_content.strip()
            if not text:
                continue
            if self._length_function(text) <= self._max_tokens:
                chunks.append(text)
            else:
                chunks.extend(
                    part for part in self._token_splitter.split_text(text) if part.strip()
                )

        if not chunks:
            # No headers matched; fall back to token-splitting the whole document.
            chunks = [part for part in self._token_splitter.split_text(markdown) if part.strip()]
        return chunks


__all__ = ["MarkdownChunker"]
