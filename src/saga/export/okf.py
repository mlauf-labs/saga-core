"""OKF bundle generation. See docs/superpowers/specs/2026-06-17-okf-export-design.md.

Turns the SAGA system of record into an Open Knowledge Format bundle: one concept file per
document (YAML frontmatter + markdown body + optional Notes), plus per-folder index.md and
log.md. FastAPI-independent; the REST route streams the builder's output as a .tar.gz.
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol

import yaml

from saga.core.logging import get_logger
from saga.core.models import EventCategory
from saga.events import EventQuery
from saga.scripts.layout import backup_basename, sanitize_component

if TYPE_CHECKING:
    from saga.core.models import Document, Event, Folder

_log = get_logger("saga.export")


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


def _bullets(entries: list[tuple[str, str, str | None]]) -> list[str]:
    lines: list[str] = []
    for text, href, desc in entries:
        suffix = f" — {desc}" if desc else ""
        lines.append(f"* [{text}]({href}){suffix}")
    return lines


def render_index(
    heading: str,
    *,
    subfolders: list[tuple[str, str, str | None]],
    documents: list[tuple[str, str, str | None]],
) -> str:
    """Render an OKF index.md (no frontmatter): heading + Subfolders + Documents bullet lists.

    Each entry is ``(link_text, bundle_relative_href, description_or_None)``.
    """
    lines: list[str] = [f"# {heading}", ""]
    if subfolders:
        lines += ["## Subfolders", *_bullets(subfolders), ""]
    if documents:
        lines += ["## Documents", *_bullets(documents), ""]
    return "\n".join(lines).rstrip() + "\n"


def render_log(heading: str, events: list[Event]) -> str:
    """Render an OKF log.md: date-grouped (newest first), each line tagged [category] type.

    Audit events group by ``recorded_at``; content events by ``occurred_at``.
    *events* arrive newest-first (recorded_at desc); per-day order is preserved.
    """
    groups: dict[str, list[Event]] = {}
    for ev in events:
        when = ev.occurred_at if ev.category == EventCategory.CONTENT else ev.recorded_at
        day = (when or ev.recorded_at).date().isoformat()
        groups.setdefault(day, []).append(ev)
    lines: list[str] = [f"# {heading}", ""]
    for day in sorted(groups, reverse=True):
        lines.append(f"## {day}")
        lines += [f"* **[{ev.category}] {ev.event_type}** — {ev.summary}" for ev in groups[day]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _folder_paths(folders: list[Folder], parents: dict[str, str | None]) -> dict[str, list[str]]:
    names = {f.folder_id: f.name for f in folders}
    out: dict[str, list[str]] = {}
    for fid in names:
        chain: list[str] = []
        cur: str | None = fid
        while cur is not None and cur in names:
            chain.append(names[cur])
            cur = parents.get(cur)
        out[fid] = list(reversed(chain))
    return out


def _concept_filename(document: Document) -> str:
    return f"{backup_basename(document.document_id, document.title)}.md"


def _original_filename(document: Document) -> str:
    suffix = PurePosixPath(document.filename).suffix
    return f"{backup_basename(document.document_id, document.title)}{suffix}"


class OkfBundleBuilder:
    """Builds an OKF bundle into a tar archive (FastAPI-independent)."""

    def __init__(
        self,
        *,
        db: DocumentSource,
        minio: BinaryReader,
        timeline: TimelineReader,
        store_name: str,
        public_base_url: str | None,
        with_originals: bool = False,
        page_size: int = 200,
    ) -> None:
        self._db = db
        self._minio = minio
        self._timeline = timeline
        self._store_name = store_name
        self._public_base_url = public_base_url
        self._with_originals = with_originals
        self._page_size = page_size

    async def write_bundle(self, tar: tarfile.TarFile) -> None:
        documents = await self._all_documents()
        folders = await self._db.list_folders()
        parents = await self._db.parents_map()
        path_by_id = _folder_paths(folders, parents)
        root = f"okf-{self._store_name}-{datetime.now(UTC):%Y%m%d_%H%M%S}"

        children: dict[str | None, list[Folder]] = {}
        for f in folders:
            children.setdefault(f.parent_id, []).append(f)
        docs_by_folder: dict[str | None, list[Document]] = {}
        for d in documents:
            docs_by_folder.setdefault(d.primary_folder_id, []).append(d)

        has_unfiled = bool(docs_by_folder.get(None))
        top = sorted(children.get(None, []), key=lambda f: f.name)
        root_subfolders = [
            (f.name, f"{sanitize_component(f.name)}/index.md", f.description) for f in top
        ]
        if has_unfiled:
            root_subfolders.append(("Unfiled", "_unfiled/index.md", None))
        self._add(
            tar, f"{root}/index.md",
            render_index("Index", subfolders=root_subfolders, documents=[]),
        )

        for folder in folders:
            parts = [sanitize_component(p) for p in path_by_id[folder.folder_id]]
            base = f"{root}/{'/'.join(parts)}"
            heading = " / ".join(path_by_id[folder.folder_id])
            subs = [
                (c.name, f"{sanitize_component(c.name)}/index.md", c.description)
                for c in sorted(children.get(folder.folder_id, []), key=lambda f: f.name)
            ]
            fdocs = docs_by_folder.get(folder.folder_id, [])
            doc_entries = [(d.title, _concept_filename(d), d.summary) for d in fdocs]
            self._add(
                tar, f"{base}/index.md",
                render_index(heading, subfolders=subs, documents=doc_entries),
            )

            events = await self._folder_events(folder.folder_id)
            if events:
                log_heading = f"Änderungsverlauf — {heading}"
                self._add(tar, f"{base}/log.md", render_log(log_heading, events))

            await self._write_documents(tar, base, fdocs)

        if has_unfiled:
            base = f"{root}/_unfiled"
            unfiled = docs_by_folder[None]
            entries = [(d.title, _concept_filename(d), d.summary) for d in unfiled]
            self._add(
                tar, f"{base}/index.md",
                render_index("Unfiled", subfolders=[], documents=entries),
            )
            await self._write_documents(tar, base, unfiled)

    async def _write_documents(
        self, tar: tarfile.TarFile, base: str, docs: list[Document]
    ) -> None:
        for d in docs:
            text = render_concept(
                d, store_name=self._store_name, public_base_url=self._public_base_url
            )
            self._add(tar, f"{base}/{_concept_filename(d)}", text)
            if self._with_originals:
                try:
                    data = await self._minio.get_object(d.document_id)
                except Exception as exc:
                    _log.warning("okf_original_missing", document_id=d.document_id, error=str(exc))
                    continue
                self._add(tar, f"{base}/{_original_filename(d)}", data)

    async def _all_documents(self) -> list[Document]:
        out: list[Document] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._db.scroll_documents(
                page_size=self._page_size, after_id=cursor
            )
            out.extend(page)
            if not cursor:
                return out

    async def _folder_events(self, folder_id: str) -> list[Event]:
        out: list[Event] = []
        offset = 0
        while True:
            page = await self._timeline.query(
                EventQuery(
                    folder_id=folder_id, include_subtree=False, limit=self._page_size, offset=offset
                )
            )
            out.extend(page)
            if len(page) < self._page_size:
                return out
            offset += self._page_size

    @staticmethod
    def _add(tar: tarfile.TarFile, path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        info = tarfile.TarInfo(name=path)
        info.size = len(data)
        info.mtime = 0  # deterministic, git-diffable bundles
        tar.addfile(info, io.BytesIO(data))
