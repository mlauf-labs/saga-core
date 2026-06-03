"""FastAPI application factory (Phase 2).

Builds the REST API with Bearer auth, actionable error handlers, and a
configurable Swagger UI (FR-34 / NFR-29). Routes are added in Phase 2.
"""

from __future__ import annotations

from fastapi import FastAPI

from docstore import __version__
from docstore.core.config import AppConfig, load_config


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or load_config()
    app = FastAPI(
        title="DocStore API",
        version=__version__,
        summary="Document store for RAG agents.",
        docs_url="/docs" if cfg.api.enable_swagger else None,
        redoc_url="/redoc" if cfg.api.enable_swagger else None,
        openapi_url="/openapi.json" if cfg.api.enable_swagger else None,
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Document, search, category, and backup routers are registered in Phase 2+.
    return app
