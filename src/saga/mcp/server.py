"""MCP server exposing search, browsing and write tools to agents (FR-19/22/23).

Runs as its own container over the Streamable HTTP transport (NFR-7), secured by a
Bearer token (FR-36). Tool descriptions are loaded from ``prompts/mcp/*.md`` (NFR-30).
Reads reuse the shared :class:`SearchService`; writes reuse the shared service layer so
agents and the REST API behave identically (and keep the projection consistent).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from saga.api import service
from saga.api.schemas import DocumentPatch
from saga.core.errors import ValidationError
from saga.core.logging import get_logger
from saga.core.models import EventCategory, ExtractedValue
from saga.events import EventQuery
from saga.llm.prompts import PromptLibrary

if TYPE_CHECKING:
    from collections.abc import Callable

    from saga.api.dependencies import Services
    from saga.core.config import AppConfig
    from saga.llm.analyzer import DocumentAnalyzer

_log = get_logger("saga.mcp")

# ---------------------------------------------------------------------------
# Folder-name validation
# ---------------------------------------------------------------------------

_FOLDER_NAME_RE = re.compile(r"^[a-zA-Z0-9_\- ]+$")
_FOLDER_NAME_ALLOWED = "letters (a-z, A-Z), digits (0-9), underscore (_), hyphen (-), and space ( )"
_FOLDER_NAME_CONSTRAINT = (
    "Only letters (a-z, A-Z), digits (0-9), underscore (_), hyphen (-), and space ( ) "
    "are allowed — regex [a-zA-Z0-9_- ]. "
    "Do NOT embed path separators such as '/' in the name; "
    "use separate create_folder calls for each hierarchy level instead."
)


def _validate_folder_name(name: str) -> str | None:
    """Return an error message string if *name* contains invalid characters, else ``None``."""
    if not name or not name.strip():
        return "Folder name must not be empty or blank."
    if not _FOLDER_NAME_RE.match(name):
        bad_chars = sorted({c for c in name if not re.match(r"[a-zA-Z0-9_\- ]", c)})
        return (
            f"Invalid folder name {name!r}: contains forbidden character(s) {bad_chars}. "
            f"Allowed characters: {_FOLDER_NAME_ALLOWED}. "
            "Do NOT use '/' or other path separators; "
            "create one folder per level with separate create_folder calls."
        )
    return None


def _description(prompts: PromptLibrary, tool: str) -> str:
    """Load a tool description from ``prompts/mcp/<tool>.md`` (NFR-30)."""
    _, body = prompts.load(f"mcp/{tool}.md")
    return body.strip()


async def _resolve_doc_type_id(services: Services, value: str) -> str:
    """Resolve a doc-type id from an id, an existing name, or by creating a new one."""
    if await services.db.get_doc_type(value) is not None:
        return value
    by_name = await services.db.get_doc_type_by_name(value)
    if by_name is not None:
        return by_name.doc_type_id
    created = await services.db.create_doc_type(name=value)
    return created.doc_type_id


def build_server(
    config: AppConfig,
    services: Services,
    prompts: PromptLibrary | None = None,
    analyzer: DocumentAnalyzer | None = None,
) -> FastMCP:
    """Build the MCP server with read + write document/folder/doc-type tools (FR-23).

    When *analyzer* is provided the ``analyze_documents_table`` tool is also
    registered; otherwise it is silently omitted so the server degrades gracefully
    when no LLM is configured.
    """
    library = prompts or PromptLibrary()
    search = services.search
    mcp: FastMCP = FastMCP(
        config.name,
        host=config.mcp.host,
        port=config.mcp.port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )

    # ----------------------------- read tools ------------------------------ #

    async def hybrid_search(
        keyword_query: Annotated[
            str | None, Field(description="query_string over documents.")
        ] = None,
        semantic_query: Annotated[
            str | None, Field(description="Natural-language semantic query over chunks.")
        ] = None,
        top_k: Annotated[int | None, Field(description="Max fused results.")] = None,
        doc_type: Annotated[str | None, Field(description="Restrict to a document type.")] = None,
        folder_id: Annotated[
            str | None, Field(description="Restrict to a folder (and its subtree).")
        ] = None,
        include_subtree: Annotated[
            bool, Field(description="Include documents in descendant folders.")
        ] = True,
        title: Annotated[str | None, Field(description="Restrict to an exact title.")] = None,
        status: Annotated[str | None, Field(description="Restrict to a status.")] = None,
        created_from: Annotated[str | None, Field(description="ISO lower bound.")] = None,
        created_to: Annotated[str | None, Field(description="ISO upper bound.")] = None,
        filters: Annotated[
            dict[str, str] | None, Field(description="Match extracted values.")
        ] = None,
        metadata: Annotated[
            dict[str, str] | None,
            Field(description="Match document metadata, e.g. {project: 'Apollo'}."),
        ] = None,
    ) -> dict[str, Any]:
        result = await search.hybrid_search(
            keyword_query=keyword_query,
            semantic_query=semantic_query,
            top_k=top_k,
            doc_type=doc_type,
            folder_id=folder_id,
            include_subtree=include_subtree,
            title=title,
            status=status,
            created_from=created_from,
            created_to=created_to,
            filters=filters,
            metadata=metadata,
        )
        return result.model_dump(mode="json")

    async def search_documents(
        query: Annotated[str | None, Field(description="Keywords; omit to browse.")] = None,
        page: Annotated[int, Field(description="1-based page number.")] = 1,
        page_size: Annotated[int, Field(description="Results per page.")] = 25,
        doc_type: Annotated[str | None, Field(description="Filter by document type.")] = None,
        folder_id: Annotated[str | None, Field(description="Filter by folder (subtree).")] = None,
        include_subtree: Annotated[bool, Field(description="Include descendants.")] = True,
        title: Annotated[str | None, Field(description="Filter by exact title.")] = None,
        status: Annotated[str | None, Field(description="Filter by status.")] = None,
        filters: Annotated[
            dict[str, str] | None, Field(description="Match extracted values.")
        ] = None,
        metadata: Annotated[
            dict[str, str] | None,
            Field(description="Match document metadata, e.g. {project: 'Apollo'}."),
        ] = None,
    ) -> dict[str, Any]:
        documents, total = await search.search_documents(
            query=query,
            page=page,
            page_size=page_size,
            doc_type=doc_type,
            folder_id=folder_id,
            include_subtree=include_subtree,
            title=title,
            status=status,
            filters=filters,
            metadata=metadata,
        )
        return {
            "items": [_doc_summary(doc) for doc in documents],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def get_document(
        document_id: Annotated[str, Field(description="The id of the document to fetch.")],
        include_content: Annotated[bool, Field(description="Include Markdown text.")] = True,
    ) -> dict[str, Any] | None:
        document = await search.get_document(document_id)
        if document is None:
            return None
        data = document.model_dump(mode="json")
        if not include_content:
            data["content_markdown"] = None
        return data

    async def get_folder_tree(
        prefix: Annotated[
            str | None, Field(description="Subtree rooted at this folder id.")
        ] = None,
        max_depth: Annotated[int | None, Field(description="Limit returned depth.")] = None,
    ) -> list[dict[str, Any]]:
        tree = await search.get_folder_tree(prefix=prefix, max_depth=max_depth)
        return [node.model_dump(mode="json") for node in tree]

    async def get_folder(
        folder_id: Annotated[str, Field(description="The id of the folder to fetch.")],
    ) -> dict[str, Any] | None:
        folder = await services.db.get_folder(folder_id)
        return folder.model_dump(mode="json") if folder is not None else None

    async def list_documents_in_folder(
        folder_id: Annotated[str, Field(description="The folder id.")],
        include_subtree: Annotated[bool, Field(description="Include descendants.")] = True,
        page: Annotated[int, Field(description="1-based page number.")] = 1,
        page_size: Annotated[int, Field(description="Results per page.")] = 25,
    ) -> dict[str, Any]:
        documents, total = await search.list_documents_in_folder(
            folder_id=folder_id, include_subtree=include_subtree, page=page, page_size=page_size
        )
        return {
            "items": [_doc_summary(doc) for doc in documents],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    async def list_doc_types() -> list[dict[str, Any]]:
        return [dt.model_dump(mode="json") for dt in await services.db.list_doc_types()]

    async def get_timeline(
        document_id: Annotated[str | None, Field(description="Restrict to one document.")] = None,
        folder_id: Annotated[
            str | None, Field(description="Restrict to a folder (and its subtree).")
        ] = None,
        category: Annotated[
            str | None, Field(description="'audit' or 'content'; omit for both.")
        ] = None,
        order_by: Annotated[
            str, Field(description="'recorded_at' (archive time) or 'occurred_at' (event time).")
        ] = "recorded_at",
        limit: Annotated[int, Field(description="Max events to return.")] = 50,
        offset: Annotated[int, Field(description="Pagination offset.")] = 0,
    ) -> dict[str, Any]:
        if services.timeline is None:
            return {"items": [], "limit": limit, "offset": offset}
        categories: tuple[EventCategory, ...] | None = None
        if category is not None:
            try:
                categories = (EventCategory(category),)
            except ValueError:
                return {"error": f"Invalid category {category!r}; expected 'audit' or 'content'."}
        query = EventQuery(
            categories=categories,
            document_id=document_id,
            folder_id=folder_id,
            order_by="occurred_at" if order_by == "occurred_at" else "recorded_at",
            limit=min(limit, services.config.timeline.max_page_size),
            offset=offset,
        )
        events = await services.timeline.query(query)
        return {
            "items": [e.model_dump(mode="json") for e in events],
            "limit": query.limit,
            "offset": query.offset,
        }

    async def get_agenda(
        folder_id: Annotated[
            str | None, Field(description="Restrict to a folder (and its subtree).")
        ] = None,
        limit: Annotated[int, Field(description="Max events to return.")] = 50,
        offset: Annotated[int, Field(description="Pagination offset.")] = 0,
    ) -> dict[str, Any]:
        if services.timeline is None:
            return {"items": [], "limit": limit, "offset": offset}
        query = EventQuery(
            categories=(EventCategory.CONTENT,),
            folder_id=folder_id,
            include_subtree=True,
            order_by="occurred_at",
            descending=False,
            expand_recurrences=True,
            limit=min(limit, services.config.timeline.max_page_size),
            offset=offset,
        )
        events = await services.timeline.query(query)
        return {
            "items": [e.model_dump(mode="json") for e in events],
            "limit": query.limit,
            "offset": query.offset,
        }

    # --------------------------- write tools ------------------------------- #

    async def update_document_metadata(
        document_id: Annotated[str, Field(description="The id of the document to update.")],
        title: Annotated[str | None, Field(description="New title.")] = None,
        summary: Annotated[str | None, Field(description="New summary.")] = None,
        doc_type: Annotated[
            str | None,
            Field(description="Doc-type id or name (created if the name is new)."),
        ] = None,
        extracted_values: Annotated[
            list[dict[str, Any]] | None,
            Field(description="Full replacement list of extracted values."),
        ] = None,
        metadata: Annotated[
            dict[str, str] | None,
            Field(description="Full replacement metadata map (string values)."),
        ] = None,
    ) -> dict[str, Any]:
        doc_type_id = await _resolve_doc_type_id(services, doc_type) if doc_type else None
        values = (
            [ExtractedValue.model_validate(v) for v in extracted_values]
            if extracted_values is not None
            else None
        )
        patch = DocumentPatch(
            title=title,
            summary=summary,
            doc_type_id=doc_type_id,
            extracted_values=values,
            metadata=metadata,
        )
        try:
            document = await service.update_document(services, document_id, patch)
        except ValidationError as exc:
            return {"error": str(exc)}
        return document.model_dump(mode="json")

    async def assign_document_to_folder(
        document_id: Annotated[str, Field(description="The document id.")],
        folder_id: Annotated[str, Field(description="The folder to add.")],
        primary: Annotated[bool, Field(description="Make this the primary folder.")] = False,
    ) -> dict[str, Any]:
        refs = await service.add_document_folder(
            services, document_id, folder_id, primary=primary, assigned_by="llm"
        )
        return {"folders": [r.model_dump(mode="json") for r in refs]}

    async def remove_document_from_folder(
        document_id: Annotated[str, Field(description="The document id.")],
        folder_id: Annotated[str, Field(description="The folder to remove.")],
    ) -> dict[str, Any]:
        refs = await service.remove_document_folder(services, document_id, folder_id)
        return {"folders": [r.model_dump(mode="json") for r in refs]}

    async def set_document_folders(
        document_id: Annotated[str, Field(description="The document id.")],
        folder_ids: Annotated[list[str], Field(description="The full folder id set.")],
        primary_id: Annotated[str | None, Field(description="Canonical folder id.")] = None,
    ) -> dict[str, Any]:
        refs = await service.set_document_folders(
            services, document_id, folder_ids=folder_ids, primary_id=primary_id, assigned_by="llm"
        )
        return {"folders": [r.model_dump(mode="json") for r in refs]}

    async def set_primary_folder(
        document_id: Annotated[str, Field(description="The document id.")],
        folder_id: Annotated[str, Field(description="The folder to make primary.")],
    ) -> dict[str, Any]:
        refs = await service.set_primary_folder(services, document_id, folder_id)
        return {"folders": [r.model_dump(mode="json") for r in refs]}

    async def create_folder(
        name: Annotated[
            str,
            Field(description=("Folder name. " + _FOLDER_NAME_CONSTRAINT)),
        ],
        description: Annotated[str | None, Field(description="Folder description.")] = None,
        parent_id: Annotated[str | None, Field(description="Parent id, or null for root.")] = None,
        metadata: Annotated[dict[str, str] | None, Field(description="Key/value metadata.")] = None,
        emoji: Annotated[
            str | None, Field(description="Single emoji for visual display (not part of the name).")
        ] = None,
    ) -> dict[str, Any]:
        error = _validate_folder_name(name)
        if error:
            return {"error": error, "allowed_characters": _FOLDER_NAME_ALLOWED}
        folder = await service.create_folder(
            services,
            name=name,
            description=description,
            parent_id=parent_id,
            metadata=metadata,
            emoji=emoji,
        )
        return folder.model_dump(mode="json")

    async def update_folder(
        folder_id: Annotated[str, Field(description="The folder id.")],
        name: Annotated[
            str | None,
            Field(description=("New name for the folder. " + _FOLDER_NAME_CONSTRAINT)),
        ] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        parent_id: Annotated[str | None, Field(description="New parent id (move).")] = None,
        metadata: Annotated[
            dict[str, str] | None, Field(description="Replacement metadata.")
        ] = None,
        emoji: Annotated[
            str | None,
            Field(description="New single emoji for visual display (not part of the name)."),
        ] = None,
    ) -> dict[str, Any]:
        if name is not None:
            error = _validate_folder_name(name)
            if error:
                return {"error": error, "allowed_characters": _FOLDER_NAME_ALLOWED}
        fields: dict[str, object] = {}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if parent_id is not None:
            fields["parent_id"] = parent_id
        if metadata is not None:
            fields["metadata"] = metadata
        if emoji is not None:
            fields["emoji"] = emoji
        folder = await service.update_folder(services, folder_id, fields=fields)
        return folder.model_dump(mode="json")

    async def delete_folder(
        folder_id: Annotated[str, Field(description="The folder id.")],
        strategy: Annotated[str, Field(description="reject | reparent | cascade.")] = "reject",
    ) -> dict[str, str]:
        await service.delete_folder(services, folder_id, strategy=strategy)
        return {"status": "deleted", "folder_id": folder_id}

    async def create_doc_type(
        name: Annotated[str, Field(description="Doc-type name.")],
        description: Annotated[str | None, Field(description="When to use this type.")] = None,
        emoji: Annotated[
            str | None, Field(description="Single emoji for visual display (not part of the name).")
        ] = None,
    ) -> dict[str, Any]:
        doc_type = await service.create_doc_type(
            services, name=name, description=description, emoji=emoji
        )
        return doc_type.model_dump(mode="json")

    async def update_doc_type(
        doc_type_id: Annotated[str, Field(description="The doc-type id.")],
        name: Annotated[str | None, Field(description="New name.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        emoji: Annotated[
            str | None,
            Field(description="New single emoji for visual display (not part of the name)."),
        ] = None,
    ) -> dict[str, Any]:
        doc_type = await service.update_doc_type(
            services, doc_type_id, name=name, description=description, emoji=emoji
        )
        return doc_type.model_dump(mode="json")

    async def delete_doc_type(
        doc_type_id: Annotated[str, Field(description="The doc-type id (must be unused).")],
    ) -> dict[str, str]:
        await service.delete_doc_type(services, doc_type_id)
        return {"status": "deleted", "doc_type_id": doc_type_id}

    async def add_document_note(
        document_id: Annotated[str, Field(description="The document id.")],
        content: Annotated[str, Field(description="Note content.")],
    ) -> dict[str, Any]:
        note = await service.add_document_note(services, document_id, content)
        return note.model_dump(mode="json")

    async def update_document_note(
        note_id: Annotated[str, Field(description="The note id.")],
        content: Annotated[str, Field(description="Replacement content.")],
    ) -> dict[str, Any]:
        note = await service.update_document_note(services, note_id, content)
        return note.model_dump(mode="json")

    async def delete_document_note(
        note_id: Annotated[str, Field(description="The note id.")],
    ) -> dict[str, str]:
        await service.delete_document_note(services, note_id)
        return {"status": "deleted", "note_id": note_id}

    async def add_folder_note(
        folder_id: Annotated[str, Field(description="The folder id.")],
        content: Annotated[str, Field(description="Note content.")],
    ) -> dict[str, Any]:
        note = await service.add_folder_note(services, folder_id, content)
        return note.model_dump(mode="json")

    async def update_folder_note(
        note_id: Annotated[str, Field(description="The note id.")],
        content: Annotated[str, Field(description="Replacement content.")],
    ) -> dict[str, Any]:
        note = await service.update_folder_note(services, note_id, content)
        return note.model_dump(mode="json")

    async def delete_folder_note(
        note_id: Annotated[str, Field(description="The note id.")],
    ) -> dict[str, str]:
        await service.delete_folder_note(services, note_id)
        return {"status": "deleted", "note_id": note_id}

    tools: list[Callable[..., Any]] = [
        hybrid_search,
        search_documents,
        get_document,
        get_folder_tree,
        get_folder,
        list_documents_in_folder,
        list_doc_types,
        get_timeline,
        get_agenda,
        update_document_metadata,
        assign_document_to_folder,
        remove_document_from_folder,
        set_document_folders,
        set_primary_folder,
        create_folder,
        update_folder,
        delete_folder,
        create_doc_type,
        update_doc_type,
        delete_doc_type,
        add_document_note,
        update_document_note,
        delete_document_note,
        add_folder_note,
        update_folder_note,
        delete_folder_note,
    ]
    for tool in tools:
        mcp.add_tool(tool, name=tool.__name__, description=_description(library, tool.__name__))

    if analyzer is not None:
        from saga.mcp.analysis_tools import build_analyze_documents_table_tool

        analysis_tool = build_analyze_documents_table_tool(services, analyzer)
        mcp.add_tool(
            analysis_tool,
            name=analysis_tool.__name__,
            description=_description(library, analysis_tool.__name__),
        )
        _log.info("mcp_server_built", tools=len(tools) + 1)
    else:
        _log.info("mcp_server_built", tools=len(tools))
    return mcp


def _doc_summary(document: Any) -> dict[str, Any]:  # noqa: ANN401 - Document model
    return {
        "document_id": document.document_id,
        "title": document.title,
        "filename": document.filename,
        "doc_type": document.doc_type,
        "summary": document.summary,
        "folder_ids": document.folder_ids,
        "created_at": document.created_at.isoformat(),
    }
