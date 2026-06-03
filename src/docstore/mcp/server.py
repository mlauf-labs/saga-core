"""MCP server exposing hybrid search + tree tools to agents (Phase 6).

Runs as its own container over the Streamable HTTP transport (NFR-7), secured by a
Bearer token (FR-36). Tool descriptions are loaded from ``prompts/mcp/*.md``
(NFR-30). The search path is optimised for low latency (FR-24 / NFR-10).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docstore.core.config import AppConfig


def build_server(config: AppConfig) -> object:
    """Build the MCP server with the document tools.

    Tools (FR-23): ``hybrid_search``, ``get_category_tree``,
    ``list_documents_in_category``, ``get_document``.
    """
    raise NotImplementedError("MCP server is implemented in Phase 6.")
