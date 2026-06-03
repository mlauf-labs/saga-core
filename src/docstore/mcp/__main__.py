"""Entry point for the MCP server (``docstore-mcp``).

Builds the FastMCP app over Streamable HTTP, wraps it with Bearer authentication
(FR-36), and serves it with uvicorn.
"""

from __future__ import annotations

import uvicorn

from docstore.core.config import load_config
from docstore.core.logging import configure_logging, get_logger
from docstore.embeddings import build_embedding_provider, load_embeddings_config
from docstore.mcp.auth import BearerAuthMiddleware
from docstore.mcp.server import build_server
from docstore.search import SearchService
from docstore.storage import OpenSearchStore


def main() -> None:
    config = load_config()
    configure_logging()
    log = get_logger("docstore.mcp")

    opensearch = OpenSearchStore(config.opensearch)
    embedder = build_embedding_provider(load_embeddings_config())
    search = SearchService(
        opensearch=opensearch,
        embedder=embedder,
        default_top_k=config.mcp.default_top_k,
        max_top_k=config.mcp.max_top_k,
    )

    mcp = build_server(config, search)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, tokens=config.security.tokens)

    log.info("mcp_server_start", host=config.mcp.host, port=config.mcp.port)
    uvicorn.run(app, host=config.mcp.host, port=config.mcp.port)


if __name__ == "__main__":
    main()
