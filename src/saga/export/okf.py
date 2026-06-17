"""OKF bundle generation. See docs/superpowers/specs/2026-06-17-okf-export-design.md.

Turns the SAGA system of record into an Open Knowledge Format bundle: one concept file per
document (YAML frontmatter + markdown body + optional Notes), plus per-folder index.md and
log.md. FastAPI-independent; the REST route streams the builder's output as a .tar.gz.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:
    from saga.core.models import Document, Event, Folder
    from saga.events import EventQuery


class DocumentSource(Protocol):
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = ...
    ) -> tuple[list[Document], str | None]: ...
    async def list_folders(self) -> list[Folder]: ...
    async def parents_map(self) -> dict[str, str | None]: ...


class BinaryReader(Protocol):
    async def get_object(self, object_name: str) -> bytes: ...


class TimelineReader(Protocol):
    async def query(self, q: EventQuery) -> list[Event]: ...


def resource_uri(document: Document, *, store_name: str, public_base_url: str | None) -> str:
    """Resolvable URL when a public base URL is configured, else an opaque saga:// URI."""
    if public_base_url:
        return f"{public_base_url.rstrip('/')}/documents/{document.document_id}/file"
    return f"saga://{store_name}/documents/{document.document_id}"


def _frontmatter(
    document: Document, *, store_name: str, public_base_url: str | None
) -> dict[str, object]:
    fm: dict[str, object] = {"type": document.doc_type or "document", "title": document.title}
    if document.summary:
        fm["description"] = document.summary
    fm["resource"] = resource_uri(document, store_name=store_name, public_base_url=public_base_url)
    tags = sorted({ref.name for ref in document.folders})
    if tags:
        fm["tags"] = tags
    fm["timestamp"] = document.updated_at.isoformat()
    fm["saga_id"] = document.document_id
    if document.doc_type_id:
        fm["saga_doc_type_id"] = document.doc_type_id
    fm["saga_content_hash"] = document.content_hash
    fm["saga_mime_type"] = document.mime_type
    fm["saga_size_bytes"] = document.size_bytes
    fm["saga_status"] = str(document.status)
    fm["saga_filename"] = document.filename
    fm["saga_created_at"] = document.created_at.isoformat()
    fm["saga_folders"] = [
        {"id": r.folder_id, "name": r.name, "primary": r.is_primary} for r in document.folders
    ]
    fm["saga_extracted_values"] = [
        {
            "key": v.key,
            "type": v.type,
            "value": v.value,
            "normalized": v.normalized,
            "confidence": v.confidence,
        }
        for v in document.extracted_values
    ]
    fm["saga_notes"] = [
        {
            "content": n.content,
            "created_at": n.created_at.isoformat(),
            "updated_at": n.updated_at.isoformat(),
        }
        for n in document.notes
    ]
    return fm


def render_concept(document: Document, *, store_name: str, public_base_url: str | None) -> str:
    """Render a concept file: YAML frontmatter, the markdown body, and an optional Notes section."""
    block = yaml.safe_dump(
        _frontmatter(document, store_name=store_name, public_base_url=public_base_url),
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    parts = [f"---\n{block}\n---\n"]
    body = document.content_markdown or ""
    if body:
        parts.append(f"\n{body}\n")
    if document.notes:
        notes = "\n".join(f"- {n.content}" for n in document.notes)
        parts.append(f"\n## Notes\n\n{notes}\n")
    return "".join(parts)
