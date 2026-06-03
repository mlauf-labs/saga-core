"""Unit tests for the conversion pipeline stage and the ingest task."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.errors import ConversionError, NotFoundError
from docstore.core.models import Document, DocumentStatus
from docstore.pipeline.stages import convert_to_markdown
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


async def test_ingest_document_success_sets_ready(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock
) -> None:
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
    }
    await ingest_document(ctx, "d1")
    statuses = [call.args[1] for call in opensearch.update_status.await_args_list]
    assert statuses == [DocumentStatus.CONVERTING, DocumentStatus.READY]


async def test_ingest_document_failure_sets_failed(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock
) -> None:
    converters.resolve.return_value.convert.side_effect = ConversionError("bad")
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
    }
    with pytest.raises(ConversionError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "bad" in last_call.kwargs["error"]


async def test_ingest_document_unexpected_error_sets_failed(
    opensearch: MagicMock, minio: MagicMock, converters: MagicMock
) -> None:
    converters.resolve.return_value.convert.side_effect = RuntimeError("kaboom")
    ctx: dict[str, Any] = {
        "opensearch": opensearch,
        "minio": minio,
        "converters": converters,
    }
    with pytest.raises(RuntimeError):
        await ingest_document(ctx, "d1")
    last_call = opensearch.update_status.await_args_list[-1]
    assert last_call.args[1] == DocumentStatus.FAILED
    assert "Unexpected error" in last_call.kwargs["error"]
