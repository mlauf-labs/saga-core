"""OKF bundle import. See docs/superpowers/specs/2026-06-17-okf-faithful-round-trip-design.md.

Reads an extracted OKF bundle directory and restores it into the SAGA system of record.
A bundle with ``saga-manifest.json`` is restored faithfully (documents, folders, doc-types,
memberships, events); a foreign OKF bundle is re-enriched through the normal pipeline.
FastAPI-independent; the REST route extracts the uploaded ``.tar.gz`` and calls the importer.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from saga.core.logging import get_logger
from saga.core.models import Document, DocumentStatus, Event, ExtractedValue
from saga.export.okf import render_notes_suffix
from saga.pipeline.queue import INDEX_JOB
from saga.scripts.layout import backup_basename

if TYPE_CHECKING:
    from saga.core.config import AppConfig

_log = get_logger("saga.import")


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


def _parse_dt(value: Any) -> datetime | None:  # noqa: ANN401
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class ImportSummary(BaseModel):
    """What an OKF import did (the REST response body)."""

    documents_imported: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    folders_created: int = 0
    folders_reused: int = 0
    doc_types_created: int = 0
    doc_types_reused: int = 0
    events_restored: int = 0
    events_skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class OkfBundleImporter:
    """Restores an extracted OKF bundle into the SAGA system of record."""

    def __init__(self, *, db: Any, minio: Any, queue: Any, config: AppConfig) -> None:  # noqa: ANN401
        self._db = db
        self._minio = minio
        self._queue = queue
        self._config = config

    # --- bundle file access ------------------------------------------------ #

    @staticmethod
    def _find(bundle_dir: Path, name: str) -> Path | None:
        return next(iter(sorted(bundle_dir.rglob(name), key=lambda p: len(p.parts))), None)

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any] | None:
        path = self._find(bundle_dir, "saga-manifest.json")
        if path is None:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def _load_events(self, bundle_dir: Path) -> list[Event]:
        path = self._find(bundle_dir, "saga-events.jsonl")
        if path is None:
            return []
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                events.append(Event.model_validate_json(stripped))
        return events

    # --- doc-types --------------------------------------------------------- #

    async def _restore_doc_types(
        self, doc_types: list[dict[str, Any]]
    ) -> tuple[dict[str, str], int, int]:
        """Ensure each manifest doc-type; return (name->new id, created, reused)."""
        existing = {dt.name for dt in await self._db.list_doc_types()}
        name_to_id: dict[str, str] = {}
        created = reused = 0
        for dt in doc_types:
            name = dt["name"]
            if name in existing:
                reused += 1
            else:
                created += 1
                existing.add(name)
            ensured = await self._db.ensure_doc_type(
                name=name, description=dt.get("description"), emoji=dt.get("emoji")
            )
            name_to_id[name] = ensured.doc_type_id
        return name_to_id, created, reused

    # --- folders ----------------------------------------------------------- #

    async def _restore_folders(
        self, folders: list[dict[str, Any]]
    ) -> tuple[dict[str, str], int, int]:
        """Create folders parents-first; return (source id->new id, created, reused).

        Idempotent by ``(parent_id, name)``: an existing folder is reused and its id is
        mapped. A folder whose parent id is unknown is created at the root (logged).
        """
        existing = await self._db.list_folders()
        by_key: dict[tuple[str | None, str], str] = {
            (f.parent_id, f.name): f.folder_id for f in existing
        }
        id_map: dict[str, str] = {}
        created = reused = 0
        pending = list(folders)
        progressed = True
        while pending and progressed:
            progressed = False
            still: list[dict[str, Any]] = []
            for f in pending:
                src_parent = f.get("parent_id")
                if src_parent is None:
                    new_parent: str | None = None
                elif src_parent in id_map:
                    new_parent = id_map[src_parent]
                else:
                    still.append(f)
                    continue
                created_now, reused_now, new_id = await self._ensure_folder(f, new_parent, by_key)
                created += created_now
                reused += reused_now
                id_map[f["id"]] = new_id
                progressed = True
            pending = still
        for f in pending:  # unresolved parents -> attach to root, non-fatal
            _log.warning(
                "okf_import_orphan_folder", folder=f.get("name"), parent=f.get("parent_id")
            )
            created_now, reused_now, new_id = await self._ensure_folder(f, None, by_key)
            created += created_now
            reused += reused_now
            id_map[f["id"]] = new_id
        return id_map, created, reused

    async def _ensure_folder(
        self,
        f: dict[str, Any],
        new_parent: str | None,
        by_key: dict[tuple[str | None, str], str],
    ) -> tuple[int, int, str]:
        key = (new_parent, f["name"])
        if key in by_key:
            return 0, 1, by_key[key]
        folder = await self._db.create_folder(
            name=f["name"],
            description=f.get("description"),
            parent_id=new_parent,
            metadata=f.get("metadata") or {},
            emoji=f.get("emoji"),
        )
        by_key[key] = folder.folder_id
        return 1, 0, folder.folder_id

    # --- documents ---------------------------------------------------------- #

    async def _restore_document(
        self,
        concept_path: Path,
        *,
        doctype_ids: dict[str, str],
        folder_map: dict[str, str],
    ) -> str:
        """Restore one concept file into the store. Returns "imported" or "skipped"."""
        text = concept_path.read_text(encoding="utf-8")  # noqa: ASYNC240
        fm, body_section = split_frontmatter(text)
        saga_id = fm.get("saga_id")
        if not saga_id:
            raise ValueError(f"Concept '{concept_path.name}' has no saga_id.")

        existing = await self._db.get_document(saga_id)
        if existing is not None:
            # On a saga_id collision, 'replace' overwrites in place; 'reject' and 'allow'
            # both skip (a preserved primary key cannot be duplicated). See spec section 7.
            if self._config.dedup.on_duplicate == "replace":
                await self._db.delete_document(saga_id)
            else:
                return "skipped"

        note_contents = [n["content"] for n in fm.get("saga_notes", [])]
        content_markdown = strip_notes_suffix(body_section, note_contents) or None
        mime_type = fm.get("saga_mime_type") or "application/octet-stream"

        data = self._read_original(concept_path, fm, saga_id)
        if data is not None:
            minio_object = await self._minio.put_object(saga_id, data, mime_type)
        else:
            data = (content_markdown or "").encode("utf-8")
            minio_object = await self._minio.put_object(saga_id, data, "text/markdown")

        now = datetime.now(UTC)
        type_name = fm.get("type", "")
        document = Document(
            document_id=saga_id,
            title=fm.get("title") or saga_id,
            filename=fm.get("saga_filename") or "",
            mime_type=mime_type,
            size_bytes=int(fm.get("saga_size_bytes") or len(data)),
            content_hash=fm.get("saga_content_hash") or hashlib.sha256(data).hexdigest(),
            minio_object=minio_object,
            status=DocumentStatus(str(fm.get("saga_status", "ready"))),
            content_markdown=content_markdown,
            doc_type_id=doctype_ids.get(type_name),
            summary=fm.get("description"),
            extracted_values=[ExtractedValue(**v) for v in fm.get("saga_extracted_values", [])],
            created_at=_parse_dt(fm.get("saga_created_at")) or now,
            updated_at=_parse_dt(fm.get("timestamp")) or now,
        )
        await self._db.create_document(document)

        for content in note_contents:
            await self._db.add_document_note(saga_id, content)

        refs = fm.get("saga_folders", [])
        folder_ids = [folder_map[r["id"]] for r in refs if r.get("id") in folder_map]
        primary = next(
            (folder_map[r["id"]] for r in refs if r.get("primary") and r.get("id") in folder_map),
            None,
        )
        if folder_ids:
            await self._db.set_document_folders(saga_id, folder_ids=folder_ids, primary_id=primary)

        await self._queue.enqueue_job(INDEX_JOB, saga_id)
        return "imported"

    # --- events ------------------------------------------------------------- #

    async def _restore_events(
        self, events: list[Event], folder_map: dict[str, str]
    ) -> tuple[int, int]:
        """Restore events verbatim, remapping ``folder_id`` via the folder id map.

        ``None`` folder_ids (content/document-scoped events) stay ``None``. Returns
        (restored, skipped); skipped counts events whose ``event_id`` already exists.
        """
        remapped = [
            event.model_copy(
                update={
                    "folder_id": folder_map.get(event.folder_id)
                    if event.folder_id is not None
                    else None
                }
            )
            for event in events
        ]
        restored = await self._db.restore_events(remapped)
        return restored, len(remapped) - restored

    # --- documents ---------------------------------------------------------- #

    def _read_original(self, concept_path: Path, fm: dict[str, Any], saga_id: str) -> bytes | None:
        """Return the original binary next to the concept, or None to use the markdown body."""
        suffix = Path(fm.get("saga_filename") or "").suffix
        if not suffix:
            return None
        base = backup_basename(saga_id, fm.get("title") or saga_id)
        original = concept_path.with_name(f"{base}{suffix}")
        return original.read_bytes() if original.exists() else None
