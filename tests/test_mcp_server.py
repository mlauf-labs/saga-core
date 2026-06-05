"""Unit tests for the MCP server build + tool handlers and bearer middleware."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from docstore.core.config import AppConfig
from docstore.core.models import CategoryNode, Document, DocumentStatus, SearchHit
from docstore.mcp.server import build_server


def _document() -> Document:
    now = datetime.now(UTC)
    return Document(
        document_id="d1",
        title="invoice.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="b/d1",
        status=DocumentStatus.READY,
        doc_type="invoice",
        category_paths=["Finance/Invoices"],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def search() -> MagicMock:
    fake = MagicMock()
    fake.hybrid_search = AsyncMock(
        return_value=[
            SearchHit(document_id="d1", chunk_id="d1:0", snippet="s", score=1.0, title="t")
        ]
    )
    fake.get_category_tree = AsyncMock(
        return_value=[CategoryNode(path="Finance", name="Finance", document_count=1)]
    )
    fake.list_documents_in_category = AsyncMock(return_value=([_document()], 1))
    fake.get_document = AsyncMock(return_value=_document())
    fake.search_documents = AsyncMock(return_value=([_document()], 1))
    fake.update_document_metadata = AsyncMock(return_value=_document())
    return fake


async def test_build_server_registers_all_tools(search: MagicMock) -> None:
    mcp = build_server(AppConfig(), search)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == {
        "hybrid_search",
        "get_category_tree",
        "list_documents_in_category",
        "get_document",
        "search_documents",
        "update_document_metadata",
    }
    # Descriptions are loaded from prompts/mcp/*.md (non-empty).
    assert all(tool.description for tool in tools)


async def test_every_tool_parameter_has_a_description(search: MagicMock) -> None:
    mcp = build_server(AppConfig(), search)
    tools = await mcp.list_tools()
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert properties, f"{tool.name} exposes no parameters"
        for name, schema in properties.items():
            assert schema.get("description"), f"{tool.name}.{name} is missing a description"


async def test_search_documents_tool(search: MagicMock) -> None:
    mcp = build_server(AppConfig(), search)
    result: Any = await mcp.call_tool("search_documents", {"query": "invoice"})
    assert result[1]["total"] == 1
    assert result[1]["items"][0]["document_id"] == "d1"


async def test_update_document_metadata_tool(search: MagicMock) -> None:
    mcp = build_server(AppConfig(), search)
    result: Any = await mcp.call_tool(
        "update_document_metadata",
        {"document_id": "d1", "doc_type": "contract", "category_paths": ["Legal"]},
    )
    assert result[1]["document_id"] == "d1"
    search.update_document_metadata.assert_awaited_once()


async def test_hybrid_search_tool(search: MagicMock) -> None:
    mcp = build_server(AppConfig(), search)
    result: Any = await mcp.call_tool("hybrid_search", {"query": "invoice"})
    structured = result[1]
    assert structured["result"][0]["document_id"] == "d1"


async def test_get_document_tool_missing(search: MagicMock) -> None:
    search.get_document.return_value = None
    mcp = build_server(AppConfig(), search)
    result: Any = await mcp.call_tool("get_document", {"document_id": "missing"})
    assert result[1]["result"] is None
