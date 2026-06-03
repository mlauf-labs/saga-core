"""Pure helpers for the backup directory layout (FR-30/31)."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

_UNSAFE = re.compile(r'[/<>:"\\|?*\x00-\x1f]')


def sanitize_component(component: str) -> str:
    """Make a single path component safe for the local filesystem."""
    cleaned = _UNSAFE.sub("_", component).strip().strip(".")
    return cleaned or "_"


def backup_relative_dir(folder_structure: list[str]) -> PurePosixPath:
    """Return the relative directory for a document from ``folder_structure[0]`` (FR-30).

    Documents without a folder structure are placed under ``_uncategorized``.
    """
    canonical = folder_structure[0] if folder_structure else "_uncategorized"
    parts = [sanitize_component(part) for part in canonical.split("/") if part.strip()]
    if not parts:
        parts = ["_uncategorized"]
    return PurePosixPath(*parts)


def backup_basename(document_id: str, title: str) -> str:
    """Return a collision-resistant base filename: ``<sanitized-title>__<id>``."""
    stem = sanitize_component(PurePosixPath(title).stem) if title else "document"
    return f"{stem}__{document_id}"


def original_filename(document_id: str, title: str) -> str:
    """Return the filename for the original binary, preserving its extension."""
    suffix = PurePosixPath(title).suffix if title else ""
    return f"{backup_basename(document_id, title)}{suffix}"


def metadata_payload(document: dict[str, Any]) -> dict[str, Any]:
    """Return the metadata sidecar payload (all fields except the bulky Markdown)."""
    return {key: value for key, value in document.items() if key != "content_markdown"}
