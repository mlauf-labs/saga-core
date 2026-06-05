"""Individual ingestion-pipeline stages (FR-4 ff.).

Stages are small, independently testable units. The worker (``tasks.ingest_document``)
orchestrates them and manages status transitions. Later phases add the analysis,
chunking, embedding and indexing stages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docstore.core.errors import NotFoundError
from docstore.core.logging import get_logger
from docstore.core.models import Chunk

if TYPE_CHECKING:
    from docstore.chunking import MarkdownChunker
    from docstore.converters import ConverterRegistry
    from docstore.embeddings import EmbeddingProvider
    from docstore.llm import DocumentAnalyzer
    from docstore.llm.schemas import AnalysisResult
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


async def analyze_metadata(
    *,
    document_id: str,
    title: str,
    markdown: str,
    opensearch: OpenSearchStore,
    analyzer: DocumentAnalyzer,
    existing_categories: list[str] | None = None,
) -> AnalysisResult:
    """Run LLM analysis and persist the extracted metadata (FR-5/14/15/16).

    ``existing_categories`` are passed to the categorisation step so the document is
    sorted into the existing folder structure where possible (FR-16).
    """
    result = await analyzer.analyze(
        title=title, content=markdown, existing_categories=existing_categories
    )
    await opensearch.update_metadata(
        document_id,
        doc_type=result.doc_type,
        extracted_values=result.extracted_values,
        folder_structure=result.folder_structure,
        category_paths=result.category_paths,
    )
    _log.info(
        "analysis_persisted",
        document_id=document_id,
        doc_type=result.doc_type,
        categories=len(result.category_paths),
    )
    return result


async def index_chunks(
    *,
    document_id: str,
    title: str,
    markdown: str,
    doc_type: str | None,
    category_paths: list[str],
    value_terms: list[str],
    opensearch: OpenSearchStore,
    chunker: MarkdownChunker,
    embedder: EmbeddingProvider,
) -> int:
    """Chunk the Markdown, embed each chunk, and index the chunk/vector records (FR-6/7/8).

    Returns the number of chunks indexed.
    """
    texts = chunker.split(markdown)
    if not texts:
        _log.warning("no_chunks", document_id=document_id)
        return 0
    vectors = await embedder.embed(texts)
    chunks = [
        Chunk(
            chunk_id=f"{document_id}:{ordinal}",
            document_id=document_id,
            ordinal=ordinal,
            snippet=text,
            embedding=vector,
            title=title,
            doc_type=doc_type,
            category_paths=category_paths,
            value_terms=value_terms,
        )
        for ordinal, (text, vector) in enumerate(zip(texts, vectors, strict=True))
    ]
    indexed = await opensearch.index_chunks(chunks)
    _log.info("chunks_indexed", document_id=document_id, count=indexed)
    return indexed
