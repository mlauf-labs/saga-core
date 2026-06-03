"""Individual ingestion-pipeline stages (FR-4 ff.).

Stages are small, independently testable units. The worker (``tasks.ingest_document``)
orchestrates them and manages status transitions. Later phases add the analysis,
chunking, embedding and indexing stages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docstore.core.errors import NotFoundError
from docstore.core.logging import get_logger

if TYPE_CHECKING:
    from docstore.converters import ConverterRegistry
    from docstore.storage import MinioStore, OpenSearchStore

_log = get_logger("docstore.pipeline.stages")


async def convert_to_markdown(
    *,
    document_id: str,
    opensearch: OpenSearchStore,
    minio: MinioStore,
    converters: ConverterRegistry,
) -> str:
    """Download the original binary, convert it to Markdown, and persist it (FR-4).

    Returns the converted Markdown text.
    """
    document = await opensearch.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Cannot convert document '{document_id}': record not found.")
    data = await minio.get_object(document_id)
    converter = converters.resolve(filename=document.title, mime_type=document.mime_type)
    _log.info(
        "conversion_start",
        document_id=document_id,
        converter=converter.name,
        mime_type=document.mime_type,
    )
    markdown = await converter.convert(
        data=data, filename=document.title, mime_type=document.mime_type
    )
    await opensearch.update_content(document_id, markdown)
    _log.info("conversion_done", document_id=document_id, chars=len(markdown))
    return markdown
