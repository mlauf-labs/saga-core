"""Converter interface implemented by Docling/Kreuzberg HTTP clients (Phase 3)."""

from __future__ import annotations

from typing import Protocol


class Converter(Protocol):
    """Converts a binary document to Markdown text."""

    name: str

    async def convert(self, *, data: bytes, filename: str, mime_type: str) -> str:
        """Convert ``data`` to Markdown. Raises ``ConversionError`` on failure."""
        ...
