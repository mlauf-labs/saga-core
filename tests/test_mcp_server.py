"""Unit tests for the MCP server build + tool handlers after the folder/doc-type refactor.

These exercise the tools registered by :func:`saga.mcp.server.build_server` end to
end against the real :class:`PostgresStore` (sqlite) and in-memory projection wired by
the ``services`` fixture. Tools are invoked through the public FastMCP ``call_tool`` API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from saga.api.dependencies import Services
from saga.core.models import Document, DocumentStatus, ExtractedValue
from saga.llm.analyzer import DocumentAnalyzer
from saga.mcp.server import build_server
from saga.storage.postgres import PostgresStore


def _as_analyzer(fake: object) -> DocumentAnalyzer:
    """Cast a duck-typed fake to the analyzer type (tests only)."""
    return cast(DocumentAnalyzer, fake)


# FastMCP's ``call_tool`` returns ``(unstructured_content, structured_content)`` when the
# tool declares a structured output schema. Tools returning a plain ``dict`` expose that
# dict directly as the structured payload; tools returning lists, ``None`` or unions are
# wrapped under a ``"result"`` key.

ALL_TOOL_NAMES = {
    "hybrid_search",
    "search_documents",
    "get_document",
    "get_folder_tree",
    "get_folder",
    "list_documents_in_folder",
    "list_doc_types",
    "get_timeline",
    "get_agenda",
    "update_document_metadata",
    "assign_document_to_folder",
    "remove_document_from_folder",
    "set_document_folders",
    "set_primary_folder",
    "create_folder",
    "update_folder",
    "delete_folder",
    "create_doc_type",
    "update_doc_type",
    "delete_doc_type",
    "add_document_note",
    "update_document_note",
    "delete_document_note",
    "add_folder_note",
    "update_folder_note",
    "delete_folder_note",
}


async def _seed_document(
    services: Services, *, title: str = "Doc.pdf", content: str = "hello world"
) -> Document:
    """Insert a ready document straight into the system of record for tests."""
    db = cast(PostgresStore, services.db)
    doc_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    document = Document(
        document_id=doc_id,
        title=title,
        filename=title,
        mime_type="application/pdf",
        size_bytes=len(content.encode()),
        content_hash=uuid.uuid4().hex,
        minio_object=f"saga-originals/{doc_id}",
        status=DocumentStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    await db.create_document(document)
    await db.update_content(doc_id, content)
    await db.update_status(doc_id, DocumentStatus.READY)
    fetched = await db.get_document(doc_id)
    assert fetched is not None
    return fetched


def _structured(result: Any) -> Any:
    """Return the structured payload from a FastMCP ``call_tool`` result tuple."""
    assert isinstance(result, tuple), f"expected (content, structured) tuple, got {result!r}"
    return result[1]


def _payload(result: Any) -> Any:
    """Unwrap the ``{"result": ...}`` envelope used for non-dict tool returns."""
    structured = _structured(result)
    if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
        return structured["result"]
    return structured


async def test_build_server_registers_all_tools(services: Services) -> None:
    mcp = build_server(services.config, services)
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == ALL_TOOL_NAMES
    # Descriptions are loaded from prompts/mcp/*.md (NFR-30) and must be non-empty.
    assert all(tool.description for tool in tools)


async def test_every_tool_parameter_has_a_description(services: Services) -> None:
    mcp = build_server(services.config, services)
    tools = await mcp.list_tools()
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        for name, schema in properties.items():
            assert schema.get("description"), f"{tool.name}.{name} is missing a description"


async def test_create_folder_then_get_folder_tree(services: Services) -> None:
    mcp = build_server(services.config, services)
    created = _structured(await mcp.call_tool("create_folder", {"name": "Finance"}))
    folder_id = created["folder_id"]
    assert created["name"] == "Finance"

    fetched = _payload(await mcp.call_tool("get_folder", {"folder_id": folder_id}))
    assert fetched["folder_id"] == folder_id

    tree = _payload(await mcp.call_tool("get_folder_tree", {}))
    assert any(node["folder_id"] == folder_id for node in tree)


async def test_create_doc_type_then_list_doc_types(services: Services) -> None:
    mcp = build_server(services.config, services)
    created = _structured(
        await mcp.call_tool("create_doc_type", {"name": "invoice", "description": "Bills."})
    )
    assert created["name"] == "invoice"

    listed = _payload(await mcp.call_tool("list_doc_types", {}))
    assert [dt["name"] for dt in listed] == ["invoice"]


async def test_get_document_returns_seeded_document(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="annual invoice")
    mcp = build_server(services.config, services)
    fetched = _payload(await mcp.call_tool("get_document", {"document_id": doc.document_id}))
    assert fetched["document_id"] == doc.document_id
    assert fetched["title"] == "invoice.pdf"


async def test_get_document_missing_returns_none(services: Services) -> None:
    mcp = build_server(services.config, services)
    fetched = _payload(await mcp.call_tool("get_document", {"document_id": "nope"}))
    assert fetched is None


async def test_assign_document_to_folder_updates_membership_and_projection(
    services: Services,
) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)
    folder = _structured(await mcp.call_tool("create_folder", {"name": "Finance"}))
    folder_id = folder["folder_id"]

    assigned = _structured(
        await mcp.call_tool(
            "assign_document_to_folder",
            {"document_id": doc.document_id, "folder_id": folder_id, "primary": True},
        )
    )
    assert [ref["folder_id"] for ref in assigned["folders"]] == [folder_id]

    # The write path re-projects the affected document to the (fake) OpenSearch store.
    projected = services.opensearch.projected[doc.document_id]  # type: ignore[attr-defined]
    assert projected["document"].folder_ids == [folder_id]


async def test_update_document_metadata_sets_summary_and_doc_type(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)
    updated = _structured(
        await mcp.call_tool(
            "update_document_metadata",
            {
                "document_id": doc.document_id,
                "summary": "An invoice for May.",
                "doc_type": "invoice",
            },
        )
    )
    assert updated["summary"] == "An invoice for May."
    assert updated["doc_type"] == "invoice"


async def test_update_document_metadata_sets_metadata(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)
    updated = _structured(
        await mcp.call_tool(
            "update_document_metadata",
            {"document_id": doc.document_id, "metadata": {"project": "Apollo"}},
        )
    )
    assert updated["metadata"] == {"project": "Apollo"}


async def test_update_document_metadata_reserved_key_returns_error(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)
    result = _structured(
        await mcp.call_tool(
            "update_document_metadata",
            {"document_id": doc.document_id, "metadata": {"saga_id": "x"}},
        )
    )
    assert "error" in result


async def test_document_note_lifecycle(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)

    note = _structured(
        await mcp.call_tool(
            "add_document_note", {"document_id": doc.document_id, "content": "Check totals."}
        )
    )
    note_id = note["note_id"]
    assert note["content"] == "Check totals."

    updated = _structured(
        await mcp.call_tool(
            "update_document_note", {"note_id": note_id, "content": "Totals verified."}
        )
    )
    assert updated["content"] == "Totals verified."

    deleted = _structured(await mcp.call_tool("delete_document_note", {"note_id": note_id}))
    assert deleted["status"] == "deleted"


async def test_hybrid_search_returns_fused_results(services: Services) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="annual invoice total")
    mcp = build_server(services.config, services)
    result = _structured(await mcp.call_tool("hybrid_search", {"keyword_query": "invoice"}))
    assert "results" in result
    assert result["results"][0]["document_id"] == doc.document_id


async def test_delete_doc_type_rejects_when_in_use_and_succeeds_when_unused(
    services: Services,
) -> None:
    doc = await _seed_document(services, title="invoice.pdf", content="x")
    mcp = build_server(services.config, services)

    # Assigning the doc-type by name creates it and links it to the document.
    _structured(
        await mcp.call_tool(
            "update_document_metadata",
            {"document_id": doc.document_id, "doc_type": "invoice"},
        )
    )
    doc_types = _payload(await mcp.call_tool("list_doc_types", {}))
    in_use_id = next(dt["doc_type_id"] for dt in doc_types if dt["name"] == "invoice")

    with pytest.raises(Exception):  # noqa: B017 - FastMCP wraps ConflictError in a ToolError
        await mcp.call_tool("delete_doc_type", {"doc_type_id": in_use_id})

    # An unused doc-type can be deleted cleanly.
    unused = _structured(await mcp.call_tool("create_doc_type", {"name": "contract"}))
    deleted = _structured(
        await mcp.call_tool("delete_doc_type", {"doc_type_id": unused["doc_type_id"]})
    )
    assert deleted["status"] == "deleted"


# ---------------------------------------------------------------------------
# analyze_documents_table tests
# ---------------------------------------------------------------------------


class _FakeAnalyzer:
    """Minimal fake for DocumentAnalyzer that records extract_fields calls."""

    def __init__(self, responses: dict[str, dict[str, str | None]] | None = None) -> None:
        # Maps document_id → field extraction result.  Falls back to returning all None.
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []

    async def extract_fields(
        self,
        *,
        content: str,
        fields: dict[str, str],
        trace_callbacks: Any = None,
    ) -> dict[str, str | None]:
        self.calls.append({"content": content, "fields": fields})
        # Try to match by content prefix so tests don't have to track doc ids.
        for key, result in self.responses.items():
            if key in content:
                return result
        return dict.fromkeys(fields)


async def test_analyze_documents_table_tool_registered_with_analyzer(
    services: Services,
) -> None:
    """analyze_documents_table appears when an analyzer is passed."""
    mcp_with = build_server(services.config, services, analyzer=_as_analyzer(_FakeAnalyzer()))
    names_with = {t.name for t in await mcp_with.list_tools()}
    assert "analyze_documents_table" in names_with

    mcp_without = build_server(services.config, services)
    names_without = {t.name for t in await mcp_without.list_tools()}
    assert "analyze_documents_table" not in names_without


async def test_analyze_documents_table_all_from_metadata_skips_llm(
    services: Services,
) -> None:
    """When all requested fields are already in extracted_values, the LLM is not called."""
    db = cast(PostgresStore, services.db)
    doc = await _seed_document(services, title="inv.pdf", content="Invoice text")
    await db.update_document(
        doc.document_id,
        extracted_values=[
            ExtractedValue(key="invoice_number", type="identifier", value="INV-001"),
            ExtractedValue(key="total_amount", type="amount", value="100 EUR"),
        ],
    )

    analyzer = _FakeAnalyzer()
    mcp = build_server(services.config, services, analyzer=_as_analyzer(analyzer))
    result = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {
                "document_ids": [doc.document_id],
                "fields": {
                    "invoice_number": "The invoice number",
                    "total_amount": "The total amount",
                },
            },
        )
    )

    assert analyzer.calls == [], "LLM should not be called when all fields are in metadata"
    assert result["summary"]["from_metadata_only"] == 1
    assert result["summary"]["analyzed_with_llm"] == 0
    rows = result["rows"]
    assert len(rows) == 1
    assert rows[0]["invoice_number"] == "INV-001"
    assert rows[0]["total_amount"] == "100 EUR"


async def test_analyze_documents_table_missing_fields_calls_llm_and_persists(
    services: Services,
) -> None:
    """Missing fields trigger an LLM call; results are merged into extracted_values."""
    db = cast(PostgresStore, services.db)
    doc = await _seed_document(services, title="contract.pdf", content="Contract signed 2026-01-15")
    # Only invoice_number is pre-stored; total_amount is missing.
    await db.update_document(
        doc.document_id,
        extracted_values=[
            ExtractedValue(key="invoice_number", type="identifier", value="INV-99"),
        ],
    )

    analyzer = _FakeAnalyzer(responses={"Contract signed": {"total_amount": "500 EUR"}})
    mcp = build_server(services.config, services, analyzer=_as_analyzer(analyzer))
    result = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {
                "document_ids": [doc.document_id],
                "fields": {
                    "invoice_number": "The invoice number",
                    "total_amount": "The total amount",
                },
            },
        )
    )

    assert result["summary"]["analyzed_with_llm"] == 1
    assert len(analyzer.calls) == 1
    # Only the missing field was passed to the LLM.
    assert list(analyzer.calls[0]["fields"].keys()) == ["total_amount"]

    rows = result["rows"]
    assert rows[0]["invoice_number"] == "INV-99"
    assert rows[0]["total_amount"] == "500 EUR"

    # Newly extracted value must be persisted back to the document.
    refreshed = await db.get_document(doc.document_id)
    assert refreshed is not None
    ev_keys = {ev.key: ev.value for ev in refreshed.extracted_values}
    assert ev_keys.get("total_amount") == "500 EUR"
    assert ev_keys.get("invoice_number") == "INV-99"


async def test_analyze_documents_table_no_content_skipped(services: Services) -> None:
    """Documents without content_markdown are counted as skipped, no LLM called."""
    db = cast(PostgresStore, services.db)
    # Seed without content so content_markdown stays None.
    doc_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    doc_bare = Document(
        document_id=doc_id,
        title="no-content.pdf",
        filename="no-content.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        content_hash=uuid.uuid4().hex,
        minio_object=f"saga-originals/{doc_id}",
        status=DocumentStatus.READY,
        created_at=now,
        updated_at=now,
    )
    await db.create_document(doc_bare)
    # Deliberately do NOT call db.update_content → content_markdown remains None.

    analyzer = _FakeAnalyzer()
    mcp = build_server(services.config, services, analyzer=_as_analyzer(analyzer))
    result = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {
                "document_ids": [doc_id],
                "fields": {"invoice_number": "The invoice number"},
            },
        )
    )

    assert analyzer.calls == []
    assert result["summary"]["skipped"] == 1
    assert result["rows"][0]["invoice_number"] is None


async def test_analyze_documents_table_folder_with_per_folder_recursive(
    services: Services,
) -> None:
    """Each folder entry uses its own recursive flag independently."""
    db = cast(PostgresStore, services.db)

    parent = _structured(
        await build_server(services.config, services).call_tool("create_folder", {"name": "Parent"})
    )
    child = _structured(
        await build_server(services.config, services).call_tool(
            "create_folder", {"name": "Child", "parent_id": parent["folder_id"]}
        )
    )

    doc_parent = await _seed_document(services, title="parent.pdf", content="Parent doc")
    doc_child = await _seed_document(services, title="child.pdf", content="Child doc")

    await db.add_document_folder(doc_parent.document_id, parent["folder_id"])
    await db.add_document_folder(doc_child.document_id, child["folder_id"])

    analyzer = _FakeAnalyzer()
    mcp = build_server(services.config, services, analyzer=_as_analyzer(analyzer))

    # non-recursive: only parent doc
    result_flat = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {
                "folders": [{"folder_id": parent["folder_id"], "recursive": False}],
                "fields": {"title_field": "Document title"},
            },
        )
    )
    flat_ids = {r["document_id"] for r in result_flat["rows"]}
    assert doc_parent.document_id in flat_ids
    assert doc_child.document_id not in flat_ids

    # recursive: both docs
    result_rec = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {
                "folders": [{"folder_id": parent["folder_id"], "recursive": True}],
                "fields": {"title_field": "Document title"},
            },
        )
    )
    rec_ids = {r["document_id"] for r in result_rec["rows"]}
    assert doc_parent.document_id in rec_ids
    assert doc_child.document_id in rec_ids


async def test_analyze_documents_table_validation_errors(services: Services) -> None:
    """Invalid inputs return descriptive error payloads without raising."""
    mcp = build_server(services.config, services, analyzer=_as_analyzer(_FakeAnalyzer()))

    # No document selection at all.
    result_no_sel = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {"fields": {"invoice_number": "The invoice number"}},
        )
    )
    assert "error" in result_no_sel

    # More than 10 fields.
    too_many = {f"field_{i}": f"desc {i}" for i in range(11)}
    result_too_many = _structured(
        await mcp.call_tool(
            "analyze_documents_table",
            {"document_ids": ["x"], "fields": too_many},
        )
    )
    assert "error" in result_too_many
