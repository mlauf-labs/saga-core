"""OKF bundle import. See docs/superpowers/specs/2026-06-17-okf-faithful-round-trip-design.md.

Reads an extracted OKF bundle directory and restores it into the SAGA system of record.
A bundle with ``saga-manifest.json`` is restored faithfully (documents, folders, doc-types,
memberships, events); a foreign OKF bundle is re-enriched through the normal pipeline.
FastAPI-independent; the REST route extracts the uploaded ``.tar.gz`` and calls the importer.
"""

from __future__ import annotations

from typing import Any

import yaml

from saga.export.okf import render_notes_suffix


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a concept file into ``(frontmatter dict, body section)``.

    The body section is everything after the closing ``---`` fence — i.e. the
    ``\\n<body>\\n`` plus an optional notes suffix that ``render_concept`` produced.
    A file without a leading ``---`` fence yields ``({}, text)``.
    """
    if not text.startswith("---\n"):
        return {}, text
    rest = text[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        return {}, text
    block = rest[:end]
    body_section = rest[end + len("\n---\n") :]
    data = yaml.safe_load(block)
    if not isinstance(data, dict):
        return {}, body_section
    return data, body_section


def strip_notes_suffix(body_section: str, note_contents: list[str]) -> str:
    """Recover ``content_markdown`` from a concept body section.

    Reverses ``render_concept``'s body assembly: removes the exact rendered notes suffix
    (when there are notes) and the single leading/trailing newline that wrapped the body.
    """
    suffix = render_notes_suffix(note_contents)
    section = body_section
    if suffix and section.endswith(suffix):
        section = section[: -len(suffix)]
    if section.startswith("\n") and section.endswith("\n"):
        return section[1:-1]
    return section
