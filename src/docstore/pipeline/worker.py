"""ARQ worker definition (NFR-11).

``WorkerSettings`` is consumed by ``arq`` to run the background worker. On startup it
loads configuration, builds the storage adapters, and bootstraps the indices/bucket
so the ingestion pipeline (Phases 3-5) has everything it needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from docstore.converters import ConverterRegistry, load_converters_config
from docstore.core.config import load_config
from docstore.core.logging import configure_logging, get_logger
from docstore.llm import (
    DocumentAnalyzer,
    PromptLibrary,
    build_llm_provider,
    load_llm_config,
)
from docstore.pipeline.queue import redis_settings
from docstore.pipeline.tasks import ingest_document
from docstore.storage import MinioStore, OpenSearchStore

if TYPE_CHECKING:
    from arq.connections import RedisSettings

_log = get_logger("docstore.pipeline.worker")


async def on_startup(ctx: dict[str, Any]) -> None:
    config = load_config()
    configure_logging()
    opensearch = OpenSearchStore(config.opensearch)
    minio = MinioStore(config.minio)
    converters = ConverterRegistry(load_converters_config())
    llm_config = load_llm_config()
    analyzer = DocumentAnalyzer(
        build_llm_provider(llm_config),
        PromptLibrary(),
        max_input_chars=llm_config.max_input_chars,
    )
    await opensearch.bootstrap()
    await minio.bootstrap()
    ctx["config"] = config
    ctx["opensearch"] = opensearch
    ctx["minio"] = minio
    ctx["converters"] = converters
    ctx["analyzer"] = analyzer
    _log.info("worker_ready")


async def on_shutdown(ctx: dict[str, Any]) -> None:
    opensearch: OpenSearchStore | None = ctx.get("opensearch")
    if opensearch is not None:
        await opensearch.close()
    converters: ConverterRegistry | None = ctx.get("converters")
    if converters is not None:
        await converters.aclose()
    analyzer: DocumentAnalyzer | None = ctx.get("analyzer")
    if analyzer is not None:
        await analyzer.aclose()
    _log.info("worker_stopped")


class WorkerSettings:
    """Settings object read by the ``arq`` CLI / ``run_worker``.

    ``redis_settings`` is assigned by the entrypoint (``configure``) so that
    configuration is not loaded at import time.
    """

    functions: ClassVar[list[Any]] = [ingest_document]
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
