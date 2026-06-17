"""ARQ worker definition (NFR-11).

``WorkerSettings`` is consumed by ``arq`` to run the background worker. On startup it
loads configuration, builds the storage adapters, and bootstraps the indices/bucket
so the ingestion pipeline (Phases 3-5) has everything it needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from saga.chunking import MarkdownChunker
from saga.converters import ConverterRegistry, load_converters_config
from saga.core.config import load_config
from saga.core.logging import configure_logging, get_logger
from saga.embeddings import build_embedding_provider, load_embeddings_config
from saga.events import EventRecorder
from saga.llm import (
    DocumentAnalyzer,
    PromptLibrary,
    build_chat_model,
    build_fallback_chat_model,
    build_step_chat_models,
    build_step_fallback_chat_models,
    load_llm_config,
)
from saga.ollama import (
    collect_required_ollama_models,
    ensure_ollama_models,
    load_ollama_runtime_config,
    resolve_check_urls,
)
from saga.pipeline.queue import redis_settings
from saga.pipeline.tasks import index_document, ingest_document
from saga.storage import MinioStore, OpenSearchStore, PostgresStore

if TYPE_CHECKING:
    from arq.connections import RedisSettings

    from saga.embeddings import EmbeddingProvider

_log = get_logger("saga.pipeline.worker")


async def on_startup(ctx: dict[str, Any]) -> None:
    config = load_config()
    configure_logging()
    opensearch = OpenSearchStore(config.opensearch)
    db = PostgresStore(config.postgres)
    minio = MinioStore(config.minio)
    converters = ConverterRegistry(load_converters_config())
    llm_config = load_llm_config()
    embeddings_config = load_embeddings_config()
    # Verify (and pull) the required models on every Ollama server before any
    # document is processed. Blocking is deliberate: jobs wait safely in Redis,
    # and analysing documents without the models would just burn ARQ retries.
    ollama_runtime = load_ollama_runtime_config()
    if ollama_runtime.startup_model_check:
        check_urls = resolve_check_urls(llm_config, embeddings_config, ollama_runtime)
        if check_urls:
            await ensure_ollama_models(
                check_urls,
                collect_required_ollama_models(llm_config, embeddings_config),
                pull_missing=ollama_runtime.pull_missing_models,
                connect_timeout=ollama_runtime.connect_timeout,
            )
    analyzer = DocumentAnalyzer(
        build_chat_model(llm_config),
        PromptLibrary(),
        fallback_model=build_fallback_chat_model(llm_config),
        step_models=build_step_chat_models(llm_config),
        step_fallbacks=build_step_fallback_chat_models(llm_config),
        max_input_chars=llm_config.max_input_chars,
        max_primary_retries=llm_config.max_primary_retries,
        max_fallback_retries=llm_config.max_fallback_retries,
        max_doctypes_in_prompt=llm_config.doctype_classification.max_doctypes_in_prompt,
        max_folders_in_prompt=llm_config.folder_placement.max_folders_in_prompt,
        output_language=config.generation.language,
        store_description=config.generation.description,
        custom_doctype_instructions=config.generation.prompt_doctype,
        custom_metadata_instructions=config.generation.prompt_metadata,
        custom_summary_instructions=config.generation.prompt_summary,
        custom_folder_instructions=config.generation.prompt_folder,
    )
    chunker = MarkdownChunker(config.chunking)
    embedder = build_embedding_provider(embeddings_config)
    if embedder.dimension != config.opensearch.vector_dimension:
        _log.warning(
            "embedding_dimension_mismatch",
            embedding_dimension=embedder.dimension,
            index_dimension=config.opensearch.vector_dimension,
            hint="Set opensearch.vector_dimension to match the embedding model and reindex.",
        )
    events = EventRecorder(db, rationale_top_n=config.timeline.rationale_top_n)
    await db.bootstrap()
    await opensearch.bootstrap()
    await minio.bootstrap()
    ctx["config"] = config
    ctx["llm_config"] = llm_config
    ctx["db"] = db
    ctx["opensearch"] = opensearch
    ctx["minio"] = minio
    ctx["converters"] = converters
    ctx["analyzer"] = analyzer
    ctx["chunker"] = chunker
    ctx["embedder"] = embedder
    ctx["events"] = events
    _log.info("worker_ready")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    opensearch: OpenSearchStore | None = ctx.get("opensearch")
    if opensearch is not None:
        await opensearch.close()
    db: PostgresStore | None = ctx.get("db")
    if db is not None:
        await db.close()
    converters: ConverterRegistry | None = ctx.get("converters")
    if converters is not None:
        await converters.aclose()
    embedder: EmbeddingProvider | None = ctx.get("embedder")
    if embedder is not None:
        await embedder.aclose()
    _log.info("worker_stopped")


class WorkerSettings:
    """Settings object read by the ``arq`` CLI / ``run_worker``.

    ``redis_settings`` is assigned by the entrypoint (``configure``) so that
    configuration is not loaded at import time.
    """

    functions: ClassVar[list[Any]] = [ingest_document, index_document]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings: ClassVar[RedisSettings | None] = None
    max_jobs = 5
    job_timeout = 1800
    max_tries = 3
    health_check_interval = 30


def configure() -> type[WorkerSettings]:
    """Populate ``WorkerSettings`` with runtime config and return it."""
    config = load_config()
    WorkerSettings.redis_settings = redis_settings(config.redis)
    return WorkerSettings
