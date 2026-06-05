"""MCP server exposing hybrid search + tree tools to agents (FR-19/22/23).

Runs as its own container over the Streamable HTTP transport (NFR-7), secured by a
Bearer token (FR-36). Tool descriptions are loaded from ``prompts/mcp/*.md`` (NFR-30).
The search path reuses the same :class:`SearchService` as the REST API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from docstore.core.logging import get_logger
from docstore.core.models import ExtractedValue
from docstore.llm.prompts import PromptLibrary

if TYPE_CHECKING:
    from docstore.core.config import AppConfig
    from docstore.search import SearchService

_log = get_logger("docstore.mcp")


def _description(prompts: PromptLibrary, tool: str) -> str:
    """Load a tool description from ``prompts/mcp/<tool>.md`` (NFR-30)."""
    _, body = prompts.load(f"mcp/{tool}.md")
    return body.strip()


def build_server(
    config: AppConfig,
    search: SearchService,
    prompts: PromptLibrary | None = None,
) -> FastMCP:
    """Build the MCP server with the document tools (FR-23).

    Tools: ``hybrid_search``, ``get_category_tree``, ``list_documents_in_category``,
    ``get_document``, ``search_documents``, ``update_document_metadata``.
    """
    library = prompts or PromptLibrary()
    mcp: FastMCP = FastMCP(
        "DocStore",
        host=config.mcp.host,
        port=config.mcp.port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )

    async def hybrid_search(
        query: Annotated[
            str, Field(description="Natural-language query or keywords; also matches titles.")
        ],
        top_k: Annotated[
            int | None, Field(description="Max snippets to return (bounded by the server max).")
        ] = None,
        doc_type: Annotated[
            str | None, Field(description="Restrict to a document type, e.g. 'invoice'.")
        ] = None,
        category_path: Annotated[
            str | None,
            Field(description="Restrict to a category subtree, e.g. 'Insurance/Health'."),
        ] = None,
        title: Annotated[
            str | None, Field(description="Restrict to an exact document title.")
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="Match extracted values, e.g. {'invoice_number': '12345'}."),
        ] = None,
    ) -> list[dict[str, Any]]:
        hits = await search.hybrid_search(
            query=query,
            top_k=top_k,
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            filters=filters,
        )
        return [hit.model_dump() for hit in hits]

    async def search_documents(
        query: Annotated[
            str | None,
            Field(description="Keywords over title/content/metadata; omit to browse with filters."),
        ] = None,
        page: Annotated[int, Field(description="1-based page number.")] = 1,
        page_size: Annotated[int, Field(description="Results per page.")] = 25,
        doc_type: Annotated[
            str | None, Field(description="Filter by document type, e.g. 'invoice'.")
        ] = None,
        category_path: Annotated[
            str | None, Field(description="Filter by a category subtree, e.g. 'Finance'.")
        ] = None,
        title: Annotated[str | None, Field(description="Filter by exact document title.")] = None,
        status: Annotated[
            str | None, Field(description="Filter by processing status, e.g. 'ready'.")
        ] = None,
        filters: Annotated[
            dict[str, str] | None,
            Field(description="Match extracted values, e.g. {'invoice_number': '12345'}."),
        ] = None,
    ) -> dict[str, Any]:
        documents, total = await search.search_documents(
            query=query,
            page=page,
            page_size=page_size,
            doc_type=doc_type,
            category_path=category_path,
            title=title,
            status=status,
            filters=filters,
        )
        return {
            "items": [
                {
                    "document_id": doc.document_id,
                    "title": doc.title,
                    "doc_type": doc.doc_type,
                    "category_paths": doc.category_paths,
                    "created_at": doc.created_at.isoformat(),
                }
                for doc in documents
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def update_document_metadata(
        document_id: Annotated[str, Field(description="The id of the document to update.")],
        doc_type: Annotated[
            str | None, Field(description="New document type label, e.g. 'invoice'.")
        ] = None,
        extracted_values: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Full replacement list of extracted values, each with "
                    "{key, type, value, normalized?, confidence?}."
                )
            ),
        ] = None,
        folder_structure: Annotated[
            list[str] | None,
            Field(description="Ordered hierarchical paths; the first is canonical."),
        ] = None,
        category_paths: Annotated[
            list[str] | None,
            Field(description="Category paths, e.g. ['Insurance/Health']."),
        ] = None,
    ) -> dict[str, Any]:
        values = (
            [ExtractedValue.model_validate(v) for v in extracted_values]
            if extracted_values is not None
            else None
        )
        document = await search.update_document_metadata(
            document_id,
            doc_type=doc_type,
            extracted_values=values,
            folder_structure=folder_structure,
            category_paths=category_paths,
        )
        return document.model_dump(mode="json")

    async def get_category_tree(
        prefix: Annotated[
            str | None, Field(description="Only return the subtree under this path.")
        ] = None,
        max_depth: Annotated[
            int | None, Field(description="Limit the returned tree depth (root is depth 1).")
        ] = None,
    ) -> list[dict[str, Any]]:
        tree = await search.get_category_tree(prefix=prefix, max_depth=max_depth)
        return [node.model_dump() for node in tree]

    async def list_documents_in_category(
        category_path: Annotated[
            str, Field(description="The category path, e.g. 'Insurance/Health'.")
        ],
        include_subtree: Annotated[
            bool, Field(description="Include documents in descendant categories.")
        ] = True,
        page: Annotated[int, Field(description="1-based page number.")] = 1,
        page_size: Annotated[int, Field(description="Results per page.")] = 25,
    ) -> dict[str, Any]:
        documents, total = await search.list_documents_in_category(
            category_path=category_path,
            include_subtree=include_subtree,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [
                {
                    "document_id": doc.document_id,
                    "title": doc.title,
                    "doc_type": doc.doc_type,
                    "category_paths": doc.category_paths,
                    "created_at": doc.created_at.isoformat(),
                }
                for doc in documents
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def get_document(
        document_id: Annotated[str, Field(description="The id of the document to fetch.")],
        include_content: Annotated[
            bool, Field(description="Include the full converted Markdown text.")
        ] = True,
    ) -> dict[str, Any] | None:
        document = await search.get_document(document_id)
        if document is None:
            return None
        data = document.model_dump(mode="json")
        if not include_content:
            data["content_markdown"] = None
        return data

    mcp.add_tool(
        hybrid_search, name="hybrid_search", description=_description(library, "hybrid_search")
    )
    mcp.add_tool(
        get_category_tree,
        name="get_category_tree",
        description=_description(library, "get_category_tree"),
    )
    mcp.add_tool(
        list_documents_in_category,
        name="list_documents_in_category",
        description=_description(library, "list_documents_in_category"),
    )
    mcp.add_tool(
        get_document, name="get_document", description=_description(library, "get_document")
    )
    mcp.add_tool(
        search_documents,
        name="search_documents",
        description=_description(library, "search_documents"),
    )
    mcp.add_tool(
        update_document_metadata,
        name="update_document_metadata",
        description=_description(library, "update_document_metadata"),
    )
    _log.info("mcp_server_built", tools=6)
    return mcp
