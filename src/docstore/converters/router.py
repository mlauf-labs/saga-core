"""Converter routing driven by ``config/converters.yaml`` (FR-3).

Resolution order: explicit extension -> MIME type -> ``default``.
Default policy: PDF -> Docling, everything else -> Kreuzberg.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class RoutingConfig(BaseModel):
    """Parsed routing section of ``converters.yaml``."""

    default: str
    by_extension: dict[str, str] = Field(default_factory=dict)
    by_mime_type: dict[str, str] = Field(default_factory=dict)


class ConverterRouter:
    """Resolves the converter service name for a given document."""

    def __init__(self, routing: RoutingConfig) -> None:
        self._routing = routing

    def resolve(self, *, filename: str, mime_type: str | None = None) -> str:
        """Return the converter service name for ``filename``/``mime_type``."""
        ext = Path(filename).suffix.lower().lstrip(".")
        if ext and ext in self._routing.by_extension:
            return self._routing.by_extension[ext]
        if mime_type and mime_type in self._routing.by_mime_type:
            return self._routing.by_mime_type[mime_type]
        return self._routing.default
