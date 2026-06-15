"""Entry point for the MCP server (``saga-mcp``).

Builds the FastMCP app over Streamable HTTP, wraps it with Bearer authentication
(FR-36), and serves it with uvicorn. Reads use OpenSearch; writes use Postgres (the
system of record) with re-projection, exactly like the REST API.

When an LLM is configured (``providers.yaml`` present and valid), a
:class:`~saga.llm.analyzer.DocumentAnalyzer` is built and passed to
:func:`~saga.mcp.server.build_server` so the ``analyze_documents_table`` tool
is available.  If LLM configuration is absent or fails to load the server starts
without the analysis tool.
"""

from __future__ import annotations

import uvicorn

from saga.api.dependencies import Services
from saga.core.config import load_config
from saga.core.errors import SagaError
from saga.core.logging import configure_logging, get_logger
from saga.embeddings import build_embedding_provider, load_embeddings_config
from saga.llm import (
    DocumentAnalyzer,
    PromptLibrary,
    build_chat_model,
    build_fallback_chat_model,
    build_step_chat_models,
    build_step_fallback_chat_models,
    load_llm_config,
)
from saga.mcp.auth import BearerAuthMiddleware
from saga.mcp.server import build_server
from saga.search import SearchService
from saga.storage import MinioStore, OpenSearchStore, PostgresStore


class _UnavailableQueue:
    """Placeholder queue: the MCP server never enqueues ingestion jobs."""

    async def enqueue_job(self, function: str, *args: object) -> object:
        raise SagaError("The MCP server cannot enqueue ingestion jobs; use the REST API.")


def _build_analyzer(config: object) -> DocumentAnalyzer | None:
    """Build a DocumentAnalyzer from the LLM config, or return None on failure."""
    from saga.core.config import AppConfig

    log = get_logger("saga.mcp")
    try:
        llm_config = load_llm_config()
        app_config = config if isinstance(config, AppConfig) else None
        analyzer = DocumentAnalyzer(
            build_chat_model(llm_config),
            PromptLibrary(),
            fallback_model=build_fallback_chat_model(llm_config),
            step_models=build_step_chat_models(llm_config),
            step_fallbacks=build_step_fallback_chat_models(llm_config),
            max_input_chars=llm_config.max_input_chars,
            max_primary_retries=llm_config.max_primary_retries,
            max_fallback_retries=llm_config.max_fallback_retries,
            output_language=app_config.generation.language if app_config else "English",
            store_description=app_config.generation.description if app_config else "",
            custom_doctype_instructions=app_config.generation.prompt_doctype if app_config else "",
            custom_metadata_instructions=app_config.generation.prompt_metadata
            if app_config
            else "",
            custom_summary_instructions=app_config.generation.prompt_summary if app_config else "",
            custom_folder_instructions=app_config.generation.prompt_folder if app_config else "",
        )
        log.info("mcp_analyzer_ready")
        return analyzer
    except Exception as exc:
        log.warning("mcp_analyzer_unavailable", reason=str(exc))
        return None


def _check_ollama_models() -> None:
    """Check-only model verification of the Ollama fleet (seconds, no pulls).

    The worker is the authoritative model puller; the MCP server only logs
    which servers are missing which models. Never blocks startup on failure.
    """
    import asyncio

    from saga.ollama import (
        collect_required_ollama_models,
        ensure_ollama_models,
        load_ollama_runtime_config,
        resolve_check_urls,
    )

    log = get_logger("saga.mcp")
    try:
        llm_config = load_llm_config()
        embeddings_config = load_embeddings_config()
        runtime = load_ollama_runtime_config()
        if not runtime.startup_model_check:
            return
        urls = resolve_check_urls(llm_config, embeddings_config, runtime)
        if urls:
            asyncio.run(
                ensure_ollama_models(
                    urls,
                    collect_required_ollama_models(llm_config, embeddings_config),
                    pull_missing=False,
                    connect_timeout=runtime.connect_timeout,
                )
            )
    except Exception as exc:
        log.warning("mcp_ollama_check_failed", reason=str(exc))


def main() -> None:
    config = load_config()
    configure_logging()
    log = get_logger("saga.mcp")

    opensearch = OpenSearchStore(config.opensearch)
    db = PostgresStore(config.postgres)
    minio = MinioStore(config.minio)
    embedder = build_embedding_provider(load_embeddings_config())
    search = SearchService(
        opensearch=opensearch,
        db=db,
        embedder=embedder,
        default_top_k=config.mcp.default_top_k,
        max_top_k=config.mcp.max_top_k,
        keyword_fields=config.opensearch.keyword_search_fields,
        keyword_default_operator=config.opensearch.keyword_default_operator,
        rrf_k=config.opensearch.rrf_k,
    )

    services = Services(
        config=config,
        db=db,
        opensearch=opensearch,
        minio=minio,
        queue=_UnavailableQueue(),
        search=search,
    )

    analyzer = _build_analyzer(config)
    _check_ollama_models()
    mcp = build_server(config, services, analyzer=analyzer)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, tokens=config.security.tokens)

    log.info("mcp_server_start", host=config.mcp.host, port=config.mcp.port)
    uvicorn.run(app, host=config.mcp.host, port=config.mcp.port)


if __name__ == "__main__":
    main()
