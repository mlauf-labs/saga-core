"""FastAPI application factory (FR-34 / NFR-29).

Builds the REST API with Bearer auth, actionable error handlers, a configurable
Swagger UI, and a lifespan that wires up the shared service container. For tests,
a pre-built ``Services`` instance can be injected to avoid any network access.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from saga import __version__
from saga.api.dependencies import Services
from saga.api.errors import register_exception_handlers
from saga.api.routes import (
    doctypes,
    documents,
    export,
    folders,
    imports,
    llm,
    search,
    stats,
    timeline,
)
from saga.core.config import AppConfig, load_config
from saga.core.logging import configure_logging, get_logger
from saga.embeddings import build_embedding_provider, load_embeddings_config
from saga.events import EventRecorder, TimelineService
from saga.llm import load_llm_config
from saga.metrics.snapshot import SnapshotService
from saga.ollama import (
    collect_required_ollama_models,
    ensure_ollama_models,
    get_pool,
    load_ollama_runtime_config,
    resolve_check_urls,
)
from saga.pipeline.queue import create_redis_pool
from saga.search import SearchService
from saga.storage import MinioStore, OpenSearchStore, PostgresStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from saga.ollama import OllamaServerPool

_log = get_logger("saga.api")


async def _ollama_server_status(
    urls: Sequence[str], pool: OllamaServerPool | None, connect_timeout: float
) -> dict[str, dict[str, Any]]:
    """Live per-server view for /health: reachability probe + current load."""
    import httpx

    async def _probe(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=connect_timeout) as client:
                response = await client.get(f"{url}/api/version")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    reachable = await asyncio.gather(*(_probe(url) for url in urls))
    snapshot = pool.snapshot() if pool is not None else {}
    status: dict[str, dict[str, Any]] = {}
    for url, up in zip(urls, reachable, strict=True):
        entry: dict[str, Any] = {"status": "up" if up else "down"}
        if url in snapshot:
            entry["in_flight"] = snapshot[url]["in_flight"]
            entry["max_concurrent"] = snapshot[url]["max_concurrent"]
        status[url] = entry
    return status


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
        db = PostgresStore(cfg.postgres)
        minio = MinioStore(cfg.minio)
        queue = await create_redis_pool(cfg.redis)
        embeddings_config = load_embeddings_config()
        embedder = build_embedding_provider(embeddings_config)
        search_service = SearchService(
            opensearch=opensearch,
            db=db,
            embedder=embedder,
            default_top_k=cfg.mcp.default_top_k,
            max_top_k=cfg.mcp.max_top_k,
            keyword_fields=cfg.opensearch.keyword_search_fields,
            keyword_default_operator=cfg.opensearch.keyword_default_operator,
            rrf_k=cfg.opensearch.rrf_k,
        )
        await db.bootstrap()
        recreated = await opensearch.bootstrap()
        if recreated:
            _log.warning(
                "search_indices_recreated_empty",
                indices=recreated,
                hint=(
                    "Search returns no results for these until rebuilt: run saga-reproject "
                    "for the document index; re-analyse documents to restore chunks."
                ),
            )
        await minio.bootstrap()
        events = EventRecorder(db, rationale_top_n=cfg.timeline.rationale_top_n)
        timeline_service = TimelineService(
            db,
            recurrence_horizon_days=cfg.timeline.recurrence_horizon_days,
            max_occurrences_per_rule=cfg.timeline.max_occurrences_per_rule,
        )
        app.state.services = Services(
            config=cfg,
            db=db,
            opensearch=opensearch,
            minio=minio,
            queue=queue,
            search=search_service,
            events=events,
            timeline=timeline_service,
        )
        app.state.snapshot = SnapshotService(db=db, opensearch=opensearch, minio=minio, redis=queue)
        # Ollama fleet visibility: remember the servers for /health and run a
        # check-only model verification in the background (the API must come up
        # immediately; the worker is the single authoritative model puller).
        llm_config = load_llm_config()
        ollama_runtime = load_ollama_runtime_config()
        ollama_urls = resolve_check_urls(llm_config, embeddings_config, ollama_runtime)
        app.state.ollama_urls = ollama_urls
        app.state.ollama_connect_timeout = ollama_runtime.connect_timeout
        if ollama_urls:
            ollama_section = llm_config if llm_config.provider == "ollama" else embeddings_config
            ollama_settings = ollama_section.providers.get("ollama")
            base_url = ollama_settings.base_url if ollama_settings is not None else None
            app.state.ollama_pool = get_pool(
                ollama_runtime.resolve_servers(base_url),
                cooldown_seconds=ollama_runtime.cooldown_seconds,
            )
        model_check_task: asyncio.Task[Any] | None = None
        if ollama_runtime.startup_model_check and ollama_urls:
            model_check_task = asyncio.create_task(
                ensure_ollama_models(
                    ollama_urls,
                    collect_required_ollama_models(llm_config, embeddings_config),
                    pull_missing=False,
                    connect_timeout=ollama_runtime.connect_timeout,
                )
            )
        _log.info("api_ready")
        try:
            yield
        finally:
            if model_check_task is not None and not model_check_task.done():
                model_check_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await model_check_task
            await opensearch.close()
            await db.close()
            await queue.aclose()
            await embedder.aclose()

    app = FastAPI(
        title=f"{cfg.name} API",
        version=__version__,
        summary="Document store for RAG agents.",
        lifespan=lifespan,
        docs_url="/docs" if cfg.api.enable_swagger else None,
        redoc_url="/redoc" if cfg.api.enable_swagger else None,
        openapi_url="/openapi.json" if cfg.api.enable_swagger else None,
    )

    if cfg.api.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.api.cors_allow_origins,
            allow_credentials=cfg.api.cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_exception_handlers(app)
    app.include_router(documents.router)
    app.include_router(folders.router)
    app.include_router(doctypes.router)
    app.include_router(search.router)
    app.include_router(export.router)
    app.include_router(imports.router)
    app.include_router(llm.router)
    app.include_router(timeline.router)
    app.include_router(stats.router)
    if cfg.metrics.enabled:
        app.include_router(stats.metrics_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        payload: dict[str, Any] = {"status": "ok", "version": __version__, "name": cfg.name}
        # Per-server Ollama status (only when an Ollama provider is configured):
        # shows which fleet members (e.g. gaming PCs) are currently on/off.
        urls: tuple[str, ...] = getattr(app.state, "ollama_urls", ())
        if urls:
            payload["ollama"] = await _ollama_server_status(
                urls,
                getattr(app.state, "ollama_pool", None),
                getattr(app.state, "ollama_connect_timeout", 5.0),
            )
        return payload

    return app
