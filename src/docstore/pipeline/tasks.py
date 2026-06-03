"""ARQ worker tasks for the ingestion pipeline (NFR-11).

The ``ingest_document`` job runs the durable pipeline, updating document status at
each stage and storing an actionable error on failure (FR-12 / NFR-14/15). Stages are
added incrementally: Phase 3 implements conversion; analysis, chunking, embedding and
indexing follow in Phases 4-5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docstore.core.errors import DocStoreError
from docstore.core.logging import bind_correlation_id, get_logger
from docstore.core.models import DocumentStatus
from docstore.pipeline.stages import convert_to_markdown

if TYPE_CHECKING:
    from docstore.converters import ConverterRegistry
    from docstore.storage import MinioStore, OpenSearchStore

_log = get_logger("docstore.pipeline.tasks")


async def ingest_document(ctx: dict[str, Any], document_id: str) -> None:
    """Run the ingestion pipeline for a previously uploaded document.

    Currently implemented stages (Phase 3): conversion to Markdown. Once all stages
    are implemented, completion sets the status to ``ready``.
    """
    bind_correlation_id(document_id)
    opensearch: OpenSearchStore = ctx["opensearch"]
    minio: MinioStore = ctx["minio"]
    converters: ConverterRegistry = ctx["converters"]

    try:
        await opensearch.update_status(document_id, DocumentStatus.CONVERTING)
        await convert_to_markdown(
            document_id=document_id,
            opensearch=opensearch,
            minio=minio,
            converters=converters,
        )
        # Analysis, chunking, embedding and indexing are added in Phases 4-5.
        await opensearch.update_status(document_id, DocumentStatus.READY)
        _log.info("ingest_complete", document_id=document_id)
    except DocStoreError as exc:
        await opensearch.update_status(document_id, DocumentStatus.FAILED, error=str(exc))
        _log.error("ingest_failed", document_id=document_id, error=str(exc))
        raise
    except Exception as exc:
        message = f"Unexpected error during ingestion: {exc}"
        await opensearch.update_status(document_id, DocumentStatus.FAILED, error=message)
        _log.error("ingest_failed_unexpected", document_id=document_id, error=str(exc))
        raise
