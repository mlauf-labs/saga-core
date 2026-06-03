"""Unit tests for the conversion pipeline stage and the ingest task."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.errors import ConversionError, NotFoundError
from docstore.core.models import Document, DocumentStatus, ExtractedValue
from docstore.llm.schemas import AnalysisResult
from docstore.pipeline.stages import analyze_metadata, convert_to_markdown
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


async def test_ingest_document_success_sets_ready(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock, analyzer: MagicMock
) -> None:
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
        "analyzer": analyzer,
    }
    await ingest_document(ctx, "d1")
    statuses = [call.args[1] for call in opensearch.update_status.await_args_list]
    assert statuses == [
        DocumentStatus.CONVERTING,
        DocumentStatus.ANALYZING,
        DocumentStatus.READY,
    ]
    analyzer.analyze.assert_awaited_once()


async def test_ingest_document_failure_sets_failed(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock, analyzer: MagicMock
) -> None:
    converters.resolve.return_value.convert.side_effect = ConversionError("bad")
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
        "analyzer": analyzer,
    }
    with pytest.raises(ConversionError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "bad" in last_call.kwargs["error"]


async def test_ingest_document_unexpected_error_sets_failed(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock, analyzer: MagicMock
) -> None:
    converters.resolve.return_value.convert.side_effect = RuntimeError("kaboom")
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
        "analyzer": analyzer,
    }
    with pytest.raises(RuntimeError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "Unexpected error" in last_call.kwargs["error"]
