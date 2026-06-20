"""ARQ worker tasks for the ingestion pipeline (NFR-11).

The ``ingest_document`` job runs the durable pipeline, updating document status at each
stage and storing an actionable error on failure (FR-12 / NFR-14/15). The pipeline:
convert -> classify doc-type -> extract values -> summarise (+embed) -> compute
similarity -> place in folder(s) -> project + index chunks.

Tracing
-------
Each pipeline run produces exactly **one** Langfuse trace containing every stage as a
named child span.  LLM stages (classify, extract, summarise, place_in_folder) expose
their LangChain generations nested under the corresponding span.  Non-LLM stages
(convert, compute_similarity, index_chunks) appear as empty spans so that latency for
every stage is captured in the trace.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from saga.core.errors import NotFoundError, SagaError
from saga.core.logging import bind_correlation_id, get_logger
from saga.core.models import DocumentStatus
from saga.llm.tracing import build_pipeline_tracer, noop_tracer
from saga.metrics.pipeline import instrumented_stage, record_ingest_result
from saga.metrics.registry import PIPELINE_DURATION
from saga.pipeline.locks import folder_placement_lock
from saga.pipeline.stages import (
    classify_doc_type,
    compute_similarity,
    convert_to_markdown,
    extract_timeline,
    extract_values,
    index_chunks,
    place_in_folder,
    summarize,
)
from saga.storage.mappings import build_value_terms

if TYPE_CHECKING:
    from saga.chunking import MarkdownChunker
    from saga.converters import ConverterRegistry
    from saga.core.config import AppConfig
    from saga.embeddings import EmbeddingProvider
    from saga.events import EventRecorder
    from saga.llm import DocumentAnalyzer
    from saga.storage import MinioStore, OpenSearchStore, PostgresStore

_log = get_logger("saga.pipeline.tasks")


async def ingest_document(ctx: dict[str, Any], document_id: str) -> None:
    """Run the full ingestion pipeline for a previously uploaded document."""
    bind_correlation_id(document_id)
    config: AppConfig = ctx["config"]
    db: PostgresStore = ctx["db"]
    opensearch: OpenSearchStore = ctx["opensearch"]
    minio: MinioStore = ctx["minio"]
    converters: ConverterRegistry = ctx["converters"]
    analyzer: DocumentAnalyzer = ctx["analyzer"]
    chunker: MarkdownChunker = ctx["chunker"]
    embedder: EmbeddingProvider = ctx["embedder"]
    llm_config = ctx["llm_config"]
    events: EventRecorder | None = ctx.get("events")

    # Use a noop placeholder so ``tracer.finish()`` in the finally clause is
    # always safe, even if the pipeline aborts before the document is fetched.
    # The real tracer (with the document title) is built as the very first step.
    tracer = noop_tracer()

    try:
        document = await db.get_document(document_id)
        if document is None:
            raise NotFoundError(f"Document '{document_id}' was not found before ingestion.")
        title = document.title
        filename = document.filename
        previous_doc_type = document.doc_type
        if events is not None:
            await events.record_doc_ingested(document_id=document_id)

        # Build the real tracer once we have the document title — this creates
        # exactly one Langfuse root trace for the entire pipeline run.
        tracer = build_pipeline_tracer(config.langfuse, document_id=document_id, title=title)
        _run_start = time.perf_counter()

        # ------------------------------------------------------------------
        # Stage 1: convert to markdown
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "convert",
            prices=config.metrics.prices,
            input={"document_id": document_id, "filename": filename},
        ):
            await db.update_status(document_id, DocumentStatus.CONVERTING)
            markdown = await convert_to_markdown(
                document_id=document_id, db=db, minio=minio, converters=converters
            )

        # ------------------------------------------------------------------
        # Stage 2: classify document type
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "classify_doc_type",
            prices=config.metrics.prices,
            input={"document_id": document_id, "title": title},
        ) as callbacks:
            await db.update_status(document_id, DocumentStatus.CLASSIFYING_TYPE)
            doc_type = await classify_doc_type(
                document_id=document_id,
                title=title,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                allow_auto_create=llm_config.doctype_classification.allow_auto_create,
                trace_callbacks=callbacks,
                previous_doc_type=previous_doc_type,
                events=events,
            )
        doc_type_name = doc_type.name if doc_type is not None else None

        # ------------------------------------------------------------------
        # Stage 3: extract field values
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "extract_values",
            prices=config.metrics.prices,
            input={"document_id": document_id, "doc_type": doc_type_name},
        ) as callbacks:
            await db.update_status(document_id, DocumentStatus.ANALYZING)
            values = await extract_values(
                document_id=document_id,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                trace_callbacks=callbacks,
            )

        # ------------------------------------------------------------------
        # Stage 3b: extract content/timeline events (dates, appointments, recurring)
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "extract_timeline",
            prices=config.metrics.prices,
            input={"document_id": document_id},
        ) as callbacks:
            await extract_timeline(
                document_id=document_id,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                min_confidence=config.timeline.content_min_confidence,
                trace_callbacks=callbacks,
            )

        # ------------------------------------------------------------------
        # Stage 4: summarise and embed
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "summarize",
            prices=config.metrics.prices,
            input={"document_id": document_id, "filename": filename},
        ) as callbacks:
            await db.update_status(document_id, DocumentStatus.SUMMARIZING)
            summary, title, summary_vector = await summarize(
                document_id=document_id,
                filename=filename,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                embedder=embedder,
                trace_callbacks=callbacks,
            )

        # ------------------------------------------------------------------
        # Stage 5: compute folder similarity
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "compute_similarity",
            prices=config.metrics.prices,
            input={"document_id": document_id, "doc_type": doc_type_name},
        ):
            await db.update_status(document_id, DocumentStatus.CLASSIFYING)
            similar, votes = await compute_similarity(
                document_id=document_id,
                summary=summary,
                summary_vector=summary_vector,
                doc_type=doc_type_name,
                value_terms=build_value_terms(values),
                opensearch=opensearch,
                db=db,
                config=config.similarity,
            )

        # ------------------------------------------------------------------
        # Stage 6: place in folder (LLM-agentic, under a global Redis lock)
        # ------------------------------------------------------------------
        async with (
            instrumented_stage(
                tracer,
                ctx["redis"],
                "place_in_folder",
                prices=config.metrics.prices,
                input={"document_id": document_id, "doc_type": doc_type_name},
            ) as callbacks,
            folder_placement_lock(ctx["redis"], config.name),
        ):
            await place_in_folder(
                document_id=document_id,
                summary=summary,
                doc_type=doc_type_name,
                extracted_values=values,
                votes=votes,
                db=db,
                analyzer=analyzer,
                allow_auto_create=llm_config.folder_placement.allow_auto_create,
                trace_callbacks=callbacks,
                similar=similar,
                events=events,
            )

        # Mark ready before projecting so the projection + chunks carry the final
        # status (search status filters read the projection, not Postgres).
        await db.update_status(document_id, DocumentStatus.READY)

        # ------------------------------------------------------------------
        # Stage 7: chunk, embed, and index
        # ------------------------------------------------------------------
        async with instrumented_stage(
            tracer,
            ctx["redis"],
            "index_chunks",
            prices=config.metrics.prices,
            input={"document_id": document_id},
        ):
            await index_chunks(
                document_id=document_id,
                summary_vector=summary_vector,
                db=db,
                opensearch=opensearch,
                chunker=chunker,
                embedder=embedder,
            )

        with contextlib.suppress(Exception):
            PIPELINE_DURATION.observe(time.perf_counter() - _run_start)
            await record_ingest_result(ctx["redis"], "success")
        _log.info("ingest_complete", document_id=document_id)
    except SagaError as exc:
        await db.update_status(document_id, DocumentStatus.FAILED, error=str(exc))
        _log.error("ingest_failed", document_id=document_id, error=str(exc))
        with contextlib.suppress(Exception):
            await record_ingest_result(ctx["redis"], "failed")
        raise
    except asyncio.CancelledError:
        # ARQ cancels timed-out jobs via CancelledError (a BaseException in Python 3.11+,
        # so it is NOT caught by `except Exception`). Mark the document as failed so it
        # doesn't remain frozen in an intermediate status indefinitely.
        await db.update_status(
            document_id,
            DocumentStatus.FAILED,
            error="Job cancelled: worker timeout or shutdown. Use re-analyze to retry.",
        )
        _log.warning("ingest_cancelled", document_id=document_id)
        with contextlib.suppress(Exception):
            await record_ingest_result(ctx["redis"], "failed")
        raise  # Let ARQ record the cancellation and apply max_tries logic.
    except Exception as exc:
        message = f"Unexpected error during ingestion: {exc}"
        await db.update_status(document_id, DocumentStatus.FAILED, error=message)
        _log.error("ingest_failed_unexpected", document_id=document_id, error=str(exc))
        with contextlib.suppress(Exception):
            await record_ingest_result(ctx["redis"], "failed")
        raise
    finally:
        tracer.finish()


async def index_document(ctx: dict[str, Any], document_id: str) -> None:
    """Re-index a document without re-running the LLM stages (used by the OKF import).

    Embeds the already-stored summary and runs ``index_chunks`` (OpenSearch document
    projection + chunk vectors). Unlike ``ingest_document`` it does not convert, classify,
    extract, summarise, or place — so restored metadata and content/timeline events are
    preserved exactly.
    """
    bind_correlation_id(document_id)
    db: PostgresStore = ctx["db"]
    opensearch: OpenSearchStore = ctx["opensearch"]
    embedder: EmbeddingProvider = ctx["embedder"]
    chunker: MarkdownChunker = ctx["chunker"]

    document = await db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found before indexing.")

    summary_vector: list[float] = []
    if document.summary:
        vectors = await embedder.embed([document.summary])
        summary_vector = vectors[0] if vectors else []

    await index_chunks(
        document_id=document_id,
        summary_vector=summary_vector,
        db=db,
        opensearch=opensearch,
        chunker=chunker,
        embedder=embedder,
    )
    _log.info("index_complete", document_id=document_id)
