"""FastAPI application factory (FR-34 / NFR-29).

Builds the REST API with Bearer auth, actionable error handlers, a configurable
Swagger UI, and a lifespan that wires up the shared service container. For tests,
a pre-built ``Services`` instance can be injected to avoid any network access.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from docstore import __version__
from docstore.api.dependencies import Services
from docstore.api.errors import register_exception_handlers
from docstore.api.routes import documents, search
from docstore.core.config import AppConfig, load_config
from docstore.core.logging import configure_logging, get_logger
from docstore.embeddings import build_embedding_provider, load_embeddings_config
from docstore.pipeline.queue import create_redis_pool
from docstore.search import SearchService
from docstore.storage import MinioStore, OpenSearchStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_log = get_logger("docstore.api")


def create_app(config: AppConfig | None = None, services: Services | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or load_config()
    injected = services

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if injected is not None:
            app.state.services = injected
            yield
            return
        configure_logging()
        opensearch = OpenSearchStore(cfg.opensearch)
        minio = MinioStore(cfg.minio)
        queue = await create_redis_pool(cfg.redis)
        embedder = build_embedding_provider(load_embeddings_config())
        search_service = SearchService(
            opensearch=opensearch,
            embedder=embedder,
            default_top_k=cfg.mcp.default_top_k,
            max_top_k=cfg.mcp.max_top_k,
        )
        await opensearch.bootstrap()
        await minio.bootstrap()
        app.state.services = Services(
            config=cfg,
            opensearch=opensearch,
            minio=minio,
            queue=queue,
            search=search_service,
        )
        _log.info("api_ready")
        try:
            yield
        finally:
            await opensearch.close()
            await queue.aclose()
            await embedder.aclose()

    app = FastAPI(
        title="DocStore API",
        version=__version__,
        summary="Document store for RAG agents.",
        lifespan=lifespan,
        docs_url="/docs" if cfg.api.enable_swagger else None,
        redoc_url="/redoc" if cfg.api.enable_swagger else None,
        openapi_url="/openapi.json" if cfg.api.enable_swagger else None,
    )

    register_exception_handlers(app)
    app.include_router(documents.router)
    app.include_router(search.router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app
