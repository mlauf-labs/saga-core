"""ARQ worker tasks for the ingestion pipeline (NFR-11).

The ``ingest_document`` job runs the durable pipeline, updating document status at
each stage and storing an actionable error on failure (FR-12 / NFR-14/15). Stages are
added incrementally: Phase 3 implements conversion; analysis, chunking, embedding and
indexing follow in Phases 4-5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docstore.core.errors import DocStoreError, NotFoundError
from docstore.core.logging import bind_correlation_id, get_logger
from docstore.core.models import DocumentStatus
from docstore.pipeline.stages import analyze_metadata, convert_to_markdown, index_chunks

if TYPE_CHECKING:
    from docstore.chunking import MarkdownChunker
    from docstore.converters import ConverterRegistry
    from docstore.embeddings import EmbeddingProvider
    from docstore.llm import DocumentAnalyzer
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
    analyzer: DocumentAnalyzer = ctx["analyzer"]
    chunker: MarkdownChunker = ctx["chunker"]
    embedder: EmbeddingProvider = ctx["embedder"]

    try:
        await opensearch.update_status(document_id, DocumentStatus.CONVERTING)
        markdown = await convert_to_markdown(
            document_id=document_id,
            opensearch=opensearch,
            minio=minio,
            converters=converters,
        )

        await opensearch.update_status(document_id, DocumentStatus.ANALYZING)
        document = await opensearch.get_document(document_id)
        if document is None:
            raise NotFoundError(f"Document '{document_id}' disappeared during ingestion.")
        analysis = await analyze_metadata(
            document_id=document_id,
            title=document.title,
            markdown=markdown,
            opensearch=opensearch,
            analyzer=analyzer,
        )

        await opensearch.update_status(document_id, DocumentStatus.INDEXING)
        await index_chunks(
            document_id=document_id,
            markdown=markdown,
            doc_type=analysis.doc_type,
            category_paths=analysis.category_paths,
            opensearch=opensearch,
            chunker=chunker,
            embedder=embedder,
        )

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
