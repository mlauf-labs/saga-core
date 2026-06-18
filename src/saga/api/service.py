"""Document/folder/doc-type management shared by the REST routes and MCP tools.

Every mutation writes to Postgres (the system of record) and then re-projects the
affected documents to OpenSearch so the search index stays consistent (FR-25). Kept
separate from the FastAPI routing layer so the logic is unit-testable with fakes.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from email.header import decode_header, make_header
from typing import TYPE_CHECKING

from saga.core.errors import ConflictError, NotFoundError, ValidationError
from saga.core.logging import get_logger
from saga.core.models import Document, DocumentStatus
from saga.okf_keys import validate_metadata_keys
from saga.pipeline.queue import INGEST_JOB
from saga.storage.postgres import ancestor_ids

if TYPE_CHECKING:
    from saga.api.dependencies import Services
    from saga.api.schemas import DocumentPatch
    from saga.core.models import DocType, Folder, FolderNode, FolderRef, Note

_log = get_logger("saga.api.service")

_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_FALLBACK_CONTENT_TYPE = "application/octet-stream"
_REPROJECT_PAGE = 200


def _actor_from_assigned_by(assigned_by: str) -> str:
    """Map the membership ``assigned_by`` value to a timeline actor."""
    return "agent" if assigned_by == "llm" else "user"


def compute_content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data`` (used for dedup, FR-13)."""
    return hashlib.sha256(data).hexdigest()


def decode_filename(filename: str) -> str:
    """Decode RFC 2047 encoded-word filenames (e.g. ``=?utf-8?B?...?=`` from emails).

    Many mail clients/importers transmit attachment names as MIME encoded-words.
    Left undecoded, the name has no usable extension, which breaks converter routing
    and MIME detection. Plain filenames (no ``=?`` marker) are returned unchanged.
    """
    if "=?" not in filename:
        return filename
    try:
        decoded = str(make_header(decode_header(filename))).strip()
    except (ValueError, UnicodeDecodeError):
        return filename
    return decoded or filename


def resolve_content_type(content_type: str | None, filename: str) -> str:
    """Resolve a usable MIME type, guessing from the filename when generic/missing."""
    if content_type and content_type.lower() not in _GENERIC_CONTENT_TYPES:
        return content_type
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or content_type or _FALLBACK_CONTENT_TYPE


def _validate_upload(data: bytes, max_bytes: int) -> None:
    if not data:
        raise ValidationError("Uploaded file is empty.")
    if len(data) > max_bytes:
        raise ValidationError(
            f"Uploaded file is {len(data)} bytes which exceeds the configured "
            f"maximum of {max_bytes} bytes."
        )


# --------------------------------------------------------------------------- #
# Projection helpers                                                            #
# --------------------------------------------------------------------------- #


async def reproject(services: Services, document_id: str) -> None:
    """Re-project a single document to OpenSearch from the system of record."""
    document = await services.db.get_document(document_id)
    if document is None:
        return
    parents = await services.db.parents_map()
    embedding = await services.db.get_summary_embedding(document_id)
    await services.opensearch.project_document(
        document,
        folder_ancestor_ids=ancestor_ids(document.folder_ids, parents),
        summary_embedding=embedding,
    )


async def _reproject_folder_subtree(services: Services, folder_id: str) -> None:
    page = 1
    while True:
        documents, total = await services.db.list_documents_in_folder(
            folder_id, include_subtree=True, page=page, page_size=_REPROJECT_PAGE
        )
        for document in documents:
            await reproject(services, document.document_id)
        if page * _REPROJECT_PAGE >= total or not documents:
            break
        page += 1


async def _reproject_affected(services: Services, document_ids: list[str]) -> None:
    for document_id in document_ids:
        await reproject(services, document_id)


async def _reproject_doc_type(services: Services, doc_type_id: str) -> None:
    page = 1
    while True:
        documents, total = await services.db.list_documents_by_doc_type(
            doc_type_id, page=page, page_size=_REPROJECT_PAGE
        )
        for document in documents:
            await reproject(services, document.document_id)
        if page * _REPROJECT_PAGE >= total or not documents:
            break
        page += 1


# --------------------------------------------------------------------------- #
# Documents                                                                     #
# --------------------------------------------------------------------------- #


async def create_document(
    services: Services,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    document_id: str | None = None,
) -> Document:
    """Validate, deduplicate, store, persist and enqueue a new document (FR-1/13)."""
    _validate_upload(data, services.config.api.max_upload_bytes)
    filename = decode_filename(filename)
    content_type = resolve_content_type(content_type, filename)
    content_hash = compute_content_hash(data)

    existing = await services.db.find_by_hash(content_hash)
    if existing is not None and document_id is None:
        policy = services.config.dedup.on_duplicate
        if policy == "reject":
            raise ConflictError(
                f"A document with identical content already exists "
                f"(document_id={existing.document_id}). Dedup policy is 'reject'."
            )
        if policy == "replace":
            await delete_document(services, existing.document_id)

    new_id = document_id or uuid.uuid4().hex
    minio_object = await services.minio.put_object(new_id, data, content_type)

    now = datetime.now(UTC)
    document = Document(
        document_id=new_id,
        title=filename,
        filename=filename,
        mime_type=content_type,
        size_bytes=len(data),
        content_hash=content_hash,
        minio_object=minio_object,
        status=DocumentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    stored = await services.db.create_document(document)
    await services.queue.enqueue_job(INGEST_JOB, new_id)
    _log.info("document_accepted", document_id=new_id, size_bytes=len(data))
    return stored


async def get_document(services: Services, document_id: str) -> Document:
    """Fetch a document or raise ``NotFoundError``."""
    document = await services.db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found.")
    return document


async def reanalyze_document(services: Services, document_id: str) -> Document:
    """Re-run the full ingestion pipeline for an existing document (FR-5/FR-11)."""
    document = await get_document(services, document_id)
    await services.db.update_status(document_id, DocumentStatus.PENDING)
    await services.queue.enqueue_job(INGEST_JOB, document_id)
    _log.info("document_reanalyze_requested", document_id=document_id)
    return document.model_copy(update={"status": DocumentStatus.PENDING, "error": None})


async def update_document(services: Services, document_id: str, patch: DocumentPatch) -> Document:
    """Apply an editable-field patch and re-project (FR-20)."""
    if patch.is_empty():
        raise ValidationError("No document fields provided to update.")
    fields = patch.model_dump(exclude_unset=True)
    if patch.metadata is not None:
        try:
            validate_metadata_keys(patch.metadata)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    document = await services.db.update_document(
        document_id,
        title=fields.get("title"),
        summary=fields.get("summary"),
        doc_type_id=fields.get("doc_type_id"),
        clear_doc_type="doc_type_id" in fields and fields.get("doc_type_id") is None,
        extracted_values=patch.extracted_values,
        metadata=patch.metadata,
    )
    await reproject(services, document_id)
    _log.info("document_updated", document_id=document_id)
    return document


async def delete_document(services: Services, document_id: str) -> None:
    """Delete a document's binary, record (cascade), and projection (FR-10/FR-26)."""
    document = await services.db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found.")
    await services.minio.remove_object(document_id)
    await services.db.delete_document(document_id)
    await services.opensearch.delete_document(document_id)
    if services.events is not None:
        await services.events.record_document_deleted(document_id=document_id, title=document.title)
    _log.info("document_deleted", document_id=document_id)


async def replace_document(
    services: Services,
    document_id: str,
    *,
    data: bytes,
    filename: str,
    content_type: str,
) -> Document:
    """Update a document by delete + re-create (FR-11)."""
    await delete_document(services, document_id)
    new_id = (
        document_id if services.config.dedup.document_id_on_update == "keep" else uuid.uuid4().hex
    )
    return await create_document(
        services, data=data, filename=filename, content_type=content_type, document_id=new_id
    )


# --------------------------------------------------------------------------- #
# Membership                                                                    #
# --------------------------------------------------------------------------- #


async def set_document_folders(
    services: Services,
    document_id: str,
    *,
    folder_ids: list[str],
    primary_id: str | None = None,
    assigned_by: str = "user",
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.set_document_folders(
        document_id, folder_ids=folder_ids, primary_id=primary_id, assigned_by=assigned_by
    )
    await reproject(services, document_id)
    if services.events is not None:
        primary = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary,
            actor=_actor_from_assigned_by(assigned_by),
        )
    return refs


async def add_document_folder(
    services: Services,
    document_id: str,
    folder_id: str,
    *,
    primary: bool = False,
    assigned_by: str = "user",
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.add_document_folder(
        document_id, folder_id, primary=primary, assigned_by=assigned_by
    )
    await reproject(services, document_id)
    if services.events is not None:
        primary_id = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary_id,
            actor=_actor_from_assigned_by(assigned_by),
        )
    return refs


async def remove_document_folder(
    services: Services, document_id: str, folder_id: str
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.remove_document_folder(document_id, folder_id)
    await reproject(services, document_id)
    if services.events is not None:
        primary = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary,
            actor="user",
        )
    return refs


async def set_primary_folder(
    services: Services, document_id: str, folder_id: str
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.set_primary_folder(document_id, folder_id)
    await reproject(services, document_id)
    if services.events is not None:
        primary = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary,
            actor="user",
        )
    return refs


# --------------------------------------------------------------------------- #
# Folders                                                                       #
# --------------------------------------------------------------------------- #


async def create_folder(
    services: Services,
    *,
    name: str,
    description: str | None = None,
    parent_id: str | None = None,
    metadata: dict[str, str] | None = None,
    emoji: str | None = None,
) -> Folder:
    folder = await services.db.create_folder(
        name=name, description=description, parent_id=parent_id, metadata=metadata, emoji=emoji
    )
    if services.events is not None:
        await services.events.record_folder_created(
            folder_id=folder.folder_id,
            name=folder.name,
            parent_id=folder.parent_id,
            actor="user",
        )
    return folder


async def get_folder(services: Services, folder_id: str) -> Folder:
    folder = await services.db.get_folder(folder_id)
    if folder is None:
        raise NotFoundError(f"Folder '{folder_id}' was not found.")
    return folder


async def update_folder(
    services: Services,
    folder_id: str,
    *,
    fields: dict[str, object],
) -> Folder:
    """Update folder fields (presence in ``fields`` decides what changes)."""
    clear_parent = "parent_id" in fields and fields.get("parent_id") is None
    old_name: str | None = None
    if services.events is not None and "name" in fields:
        existing = await services.db.get_folder(folder_id)
        old_name = existing.name if existing is not None else None
    folder = await services.db.update_folder(
        folder_id,
        name=fields.get("name"),  # type: ignore[arg-type]
        description=fields.get("description"),  # type: ignore[arg-type]
        parent_id=fields.get("parent_id"),  # type: ignore[arg-type]
        clear_parent=clear_parent,
        metadata=fields.get("metadata"),  # type: ignore[arg-type]
        emoji=fields.get("emoji"),  # type: ignore[arg-type]
    )
    # Rename/move changes folder names + ancestor ids denormalised on the projection.
    await _reproject_folder_subtree(services, folder_id)
    if services.events is not None and old_name is not None and folder.name != old_name:
        await services.events.record_folder_renamed(
            folder_id=folder.folder_id,
            old_name=old_name,
            new_name=folder.name,
            actor="user",
        )
    return folder


async def delete_folder(services: Services, folder_id: str, *, strategy: str = "reject") -> None:
    folder = await services.db.get_folder(folder_id)
    if folder is None:
        raise NotFoundError(f"Folder '{folder_id}' was not found.")
    affected = await services.db.delete_folder(folder_id, strategy=strategy)
    await _reproject_affected(services, affected)
    if services.events is not None:
        await services.events.record_folder_deleted(
            folder_id=folder_id, name=folder.name, strategy=strategy, affected=len(affected)
        )


async def folder_tree(
    services: Services, *, prefix: str | None = None, max_depth: int | None = None
) -> list[FolderNode]:
    return await services.db.folder_tree(prefix=prefix, max_depth=max_depth)


# --------------------------------------------------------------------------- #
# Notes                                                                         #
# --------------------------------------------------------------------------- #


async def add_document_note(services: Services, document_id: str, content: str) -> Note:
    return await services.db.add_document_note(document_id, content)


async def update_document_note(services: Services, note_id: str, content: str) -> Note:
    return await services.db.update_document_note(note_id, content)


async def delete_document_note(services: Services, note_id: str) -> None:
    await services.db.delete_document_note(note_id)


async def add_folder_note(services: Services, folder_id: str, content: str) -> Note:
    return await services.db.add_folder_note(folder_id, content)


async def update_folder_note(services: Services, note_id: str, content: str) -> Note:
    return await services.db.update_folder_note(note_id, content)


async def delete_folder_note(services: Services, note_id: str) -> None:
    await services.db.delete_folder_note(note_id)


# --------------------------------------------------------------------------- #
# Doc-types                                                                     #
# --------------------------------------------------------------------------- #


async def create_doc_type(
    services: Services, *, name: str, description: str | None = None, emoji: str | None = None
) -> DocType:
    return await services.db.create_doc_type(name=name, description=description, emoji=emoji)


async def get_doc_type(services: Services, doc_type_id: str) -> DocType:
    doc_type = await services.db.get_doc_type(doc_type_id)
    if doc_type is None:
        raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
    return doc_type


async def update_doc_type(
    services: Services,
    doc_type_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    emoji: str | None = None,
) -> DocType:
    doc_type = await services.db.update_doc_type(
        doc_type_id, name=name, description=description, emoji=emoji
    )
    if name is not None:
        # The doc-type name is denormalised onto the projection of every document.
        await _reproject_doc_type(services, doc_type_id)
    return doc_type


async def delete_doc_type(services: Services, doc_type_id: str) -> None:
    """Delete a doc-type only when unused (otherwise 409, FR-14)."""
    doc_type = await services.db.get_doc_type(doc_type_id)
    if doc_type is None:
        raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
    await services.db.delete_doc_type(doc_type_id)
    if services.events is not None:
        await services.events.record_doc_type_deleted(doc_type_id=doc_type_id, name=doc_type.name)


async def list_documents_by_doc_type(
    services: Services, doc_type_id: str, *, page: int, page_size: int
) -> tuple[list[Document], int]:
    if await services.db.get_doc_type(doc_type_id) is None:
        raise NotFoundError(f"Doc-type '{doc_type_id}' was not found.")
    return await services.db.list_documents_by_doc_type(doc_type_id, page=page, page_size=page_size)
