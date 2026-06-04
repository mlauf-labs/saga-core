"""Unit tests for the conversion pipeline stage and the ingest task."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.errors import ConversionError, NotFoundError
from docstore.core.models import Document, DocumentStatus, ExtractedValue
from docstore.llm.schemas import AnalysisResult
from docstore.pipeline.stages import (
    analyze_metadata,
    convert_to_markdown,
    index_chunks,
)
from docstore.pipeline.tasks import ingest_document


def _document() -> Document:
    now = datetime.now(UTC)
    return Document(
        document_id="d1",
        title="invoice.pdf",
        mime_type="application/pdf",
        size_bytes=4,
        content_hash="h",
        minio_object="b/d1",
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def opensearch() -> MagicMock:
    store = MagicMock()
    store.get_document = AsyncMock(return_value=_document())
    store.update_content = AsyncMock()
    store.update_status = AsyncMock()
    store.update_metadata = AsyncMock()
    store.index_chunks = AsyncMock(return_value=2)
    return store


@pytest.fixture
def minio() -> MagicMock:
    store = MagicMock()
    store.get_object = AsyncMock(return_value=b"%PDF-binary")
    return store


@pytest.fixture
def converters() -> MagicMock:
    converter = MagicMock()
    converter.name = "docling"
    converter.convert = AsyncMock(return_value="# Markdown")
    registry = MagicMock()
    registry.resolve = MagicMock(return_value=converter)
    return registry


@pytest.fixture
def analyzer() -> MagicMock:
    result = AnalysisResult(
        doc_type="invoice",
        extracted_values=[ExtractedValue(key="invoice_number", type="identifier", value="1")],
        folder_structure=["Finance/Invoices"],
        category_paths=["Finance/Invoices"],
    )
    fake = MagicMock()
    fake.analyze = AsyncMock(return_value=result)
    return fake


@pytest.fixture
def chunker() -> MagicMock:
    fake = MagicMock()
    fake.split = MagicMock(return_value=["chunk one", "chunk two"])
    return fake


@pytest.fixture
def embedder() -> MagicMock:
    fake = MagicMock()
    fake.embed = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])
    return fake


async def test_convert_to_markdown_persists_content(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock
) -> None:
    result = await convert_to_markdown(
        document_id="d1", opensearch=opensearch, minio=minio, converters=converters
    )
    assert result == "# Markdown"
    opensearch.update_content.assert_awaited_once_with("d1", "# Markdown")
    converters.resolve.assert_called_once_with(filename="invoice.pdf", mime_type="application/pdf")


async def test_convert_missing_document_raises(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock
) -> None:
    opensearch.get_document.return_value = None
    with pytest.raises(NotFoundError):
        await convert_to_markdown(
            document_id="d1", opensearch=opensearch, minio=minio, converters=converters
        )


async def test_analyze_metadata_persists(opensearch: MagicMock, analyzer: MagicMock) -> None:
    result = await analyze_metadata(
        document_id="d1",
        title="inv.pdf",
        markdown="# md",
        opensearch=opensearch,
        analyzer=analyzer,
    )
    assert result.doc_type == "invoice"
    opensearch.update_metadata.assert_awaited_once()
    kwargs = opensearch.update_metadata.await_args.kwargs
    assert kwargs["doc_type"] == "invoice"
    assert kwargs["folder_structure"] == ["Finance/Invoices"]


async def test_index_chunks_builds_and_indexes(
    opensearch: MagicMock, chunker: MagicMock, embedder: MagicMock
) -> None:
    indexed = await index_chunks(
        document_id="d1",
        title="Invoice.pdf",
        markdown="# md",
        doc_type="invoice",
        category_paths=["Finance"],
        value_terms=["invoice_number=INV-1"],
        opensearch=opensearch,
        chunker=chunker,
        embedder=embedder,
    )
    assert indexed == 2
    chunks = opensearch.index_chunks.await_args.args[0]
    assert [c.chunk_id for c in chunks] == ["d1:0", "d1:1"]
    assert chunks[0].embedding == [0.1, 0.2]
    assert chunks[1].doc_type == "invoice"
    assert chunks[0].value_terms == ["invoice_number=INV-1"]
    assert chunks[0].title == "Invoice.pdf"


async def test_index_chunks_no_chunks_returns_zero(
    opensearch: MagicMock, chunker: MagicMock, embedder: MagicMock
) -> None:
    chunker.split.return_value = []
    indexed = await index_chunks(
        document_id="d1",
        title="empty.txt",
        markdown="",
        doc_type=None,
        category_paths=[],
        value_terms=[],
        opensearch=opensearch,
        chunker=chunker,
        embedder=embedder,
    )
    assert indexed == 0
    embedder.embed.assert_not_awaited()
    opensearch.index_chunks.assert_not_awaited()


def _full_ctx(
    opensearch: MagicMock,
    minio: MagicMock,
    converters: MagicMock,
    analyzer: MagicMock,
    chunker: MagicMock,
    embedder: MagicMock,
) -> dict[str, Any]:
    return {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
        "analyzer": analyzer,
        "chunker": chunker,
        "embedder": embedder,
    }


async def test_ingest_document_success_sets_ready(
    opensearch: MagicMock,
    minio: MagicMock,
    converters: MagicMock,
    analyzer: MagicMock,
    chunker: MagicMock,
    embedder: MagicMock,
) -> None:
    ctx = _full_ctx(opensearch, minio, converters, analyzer, chunker, embedder)
    await ingest_document(ctx, "d1")
    statuses = [call.args[1] for call in opensearch.update_status.await_args_list]
    assert statuses == [
        DocumentStatus.CONVERTING,
        DocumentStatus.ANALYZING,
        DocumentStatus.INDEXING,
        DocumentStatus.READY,
    ]
    analyzer.analyze.assert_awaited_once()
    opensearch.index_chunks.assert_awaited_once()


async def test_ingest_document_failure_sets_failed(
    opensearch: MagicMock,
    minio: MagicMock,
    converters: MagicMock,
    analyzer: MagicMock,
    chunker: MagicMock,
    embedder: MagicMock,
) -> None:
    converters.resolve.return_value.convert.side_effect = ConversionError("bad")
    ctx = _full_ctx(opensearch, minio, converters, analyzer, chunker, embedder)
    with pytest.raises(ConversionError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "bad" in last_call.kwargs["error"]


async def test_ingest_document_unexpected_error_sets_failed(
    opensearch: MagicMock,
    minio: MagicMock,
    converters: MagicMock,
    analyzer: MagicMock,
    chunker: MagicMock,
    embedder: MagicMock,
) -> None:
    converters.resolve.return_value.convert.side_effect = RuntimeError("kaboom")
    ctx = _full_ctx(opensearch, minio, converters, analyzer, chunker, embedder)
    with pytest.raises(RuntimeError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "Unexpected error" in last_call.kwargs["error"]
