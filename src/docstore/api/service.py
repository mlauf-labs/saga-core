"""Document management operations shared by the REST routes (FR-1/10/11/13).

Kept separate from the FastAPI routing layer so the logic is unit-testable with
mocked services.
"""

from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from docstore.core.errors import ConflictError, NotFoundError, ValidationError
from docstore.core.logging import get_logger
from docstore.core.models import Document, DocumentStatus
from docstore.pipeline.queue import INGEST_JOB

if TYPE_CHECKING:
    from docstore.api.dependencies import Services
    from docstore.api.schemas import DocumentMetadataPatch

_log = get_logger("docstore.api.service")

_GENERIC_CONTENT_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
_FALLBACK_CONTENT_TYPE = "application/octet-stream"


def compute_content_hash(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data`` (used for dedup, FR-13)."""
    return hashlib.sha256(data).hexdigest()


def resolve_content_type(content_type: str | None, filename: str) -> str:
    """Resolve a usable MIME type, guessing from the filename when generic/missing.

    Many clients upload with a generic ``application/octet-stream`` type; converters
    rely on a meaningful type, so we infer it from the file extension when needed.
    """
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


async def create_document(
    services: Services,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    document_id: str | None = None,
) -> Document:
    """Validate, deduplicate, store, index and enqueue a new document (FR-1/13)."""
    _validate_upload(data, services.config.api.max_upload_bytes)
    content_type = resolve_content_type(content_type, filename)
    content_hash = compute_content_hash(data)

    existing = await services.opensearch.find_by_hash(content_hash)
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
    object_name = new_id
    minio_object = await services.minio.put_object(object_name, data, content_type)

    now = datetime.now(UTC)
    document = Document(
        document_id=new_id,
        title=filename,
        mime_type=content_type,
        size_bytes=len(data),
        content_hash=content_hash,
        minio_object=minio_object,
        status=DocumentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    await services.opensearch.index_document(document)
    await services.queue.enqueue_job(INGEST_JOB, new_id)
    _log.info("document_accepted", document_id=new_id, size_bytes=len(data))
    return document


async def get_document(services: Services, document_id: str) -> Document:
    """Fetch a document or raise ``NotFoundError``."""
    document = await services.opensearch.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found.")
    return document


async def reanalyze_document(services: Services, document_id: str) -> Document:
    """Re-run the full ingestion pipeline for an existing document (FR-5/FR-11).

    Re-converts the stored binary and regenerates metadata, chunks and embeddings.
    The document id and stored binary are kept; the status is reset to ``pending`` and
    the ingestion job is re-enqueued.
    """
    document = await get_document(services, document_id)
    await services.opensearch.update_status(document_id, DocumentStatus.PENDING)
    await services.queue.enqueue_job(INGEST_JOB, document_id)
    _log.info("document_reanalyze_requested", document_id=document_id)
    return document.model_copy(update={"status": DocumentStatus.PENDING, "error": None})


async def update_metadata(
    services: Services, document_id: str, patch: DocumentMetadataPatch
) -> Document:
    """Apply a metadata patch and propagate to chunks (FR-20).

    Only fields set on ``patch`` are changed.
    """
    if patch.is_empty():
        raise ValidationError("No metadata fields provided to update.")
    document = await services.opensearch.update_document_fields(
        document_id,
        doc_type=patch.doc_type,
        extracted_values=patch.extracted_values,
        folder_structure=patch.folder_structure,
        category_paths=patch.category_paths,
    )
    _log.info("document_metadata_updated", document_id=document_id)
    return document


async def delete_document(services: Services, document_id: str) -> None:
    """Delete a document's binary, record, and chunks (FR-10/FR-26)."""
    document = await services.opensearch.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found.")
    await services.minio.remove_object(document_id)
    await services.opensearch.delete_document(document_id)
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
        services,
        data=data,
        filename=filename,
        content_type=content_type,
        document_id=new_id,
    )
