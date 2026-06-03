"""Unit tests for the Markdown chunker (FR-6)."""

from __future__ import annotations

from docstore.chunking import MarkdownChunker
from docstore.core.config import ChunkingConfig


def _word_count(text: str) -> int:
    return len(text.split())


def _chunker(max_tokens: int = 50, overlap: int = 5) -> MarkdownChunker:
    config = ChunkingConfig(max_tokens=max_tokens, overlap_tokens=overlap)
    return MarkdownChunker(config, length_function=_word_count)


def test_blank_input_returns_empty() -> None:
    assert _chunker().split("   \n  ") == []


def test_splits_by_headers() -> None:
    markdown = "# A\n\nAlpha text.\n\n## B\n\nBeta text."
    chunks = _chunker(max_tokens=100).split(markdown)
    assert len(chunks) == 2
    assert any("Alpha" in c for c in chunks)
    assert any("Beta" in c for c in chunks)


def test_long_section_is_token_split() -> None:
    body = " ".join(f"word{i}" for i in range(120))
    markdown = f"# Title\n\n{body}"
    chunks = _chunker(max_tokens=30, overlap=5).split(markdown)
    assert len(chunks) > 1
    for chunk in chunks:
        assert _word_count(chunk) <= 30


def test_no_headers_falls_back_to_token_split() -> None:
    body = " ".join(f"w{i}" for i in range(80))
    chunks = _chunker(max_tokens=20).split(body)
    assert len(chunks) > 1


def test_short_plain_text_single_chunk() -> None:
    chunks = _chunker(max_tokens=100).split("just a short note")
    assert chunks == ["just a short note"]
