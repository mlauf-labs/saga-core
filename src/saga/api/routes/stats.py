"""Statistics endpoints: JSON /stats (authed) and Prometheus /metrics (unauthed)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest
from pydantic import BaseModel

from saga.api.dependencies import AuthDep, ServicesDep, SnapshotDep
from saga.metrics.redis_aggregate import PipelineAggregates, read_pipeline_aggregates
from saga.metrics.registry import SAGA_REGISTRY
from saga.metrics.snapshot import Snapshot

# Snapshot gauges live only in the API process (the worker exposes cumulative metrics).
G_DOCS_TOTAL = Gauge("saga_documents_total", "Total documents.", registry=SAGA_REGISTRY)
G_DOCS_STATUS = Gauge(
    "saga_documents_by_status", "Documents by status.", ("status",), registry=SAGA_REGISTRY
)
G_DOCS_DOCTYPE = Gauge(
    "saga_documents_by_doc_type",
    "Documents by doc-type.",
    ("doc_type",),
    registry=SAGA_REGISTRY,
)
G_DOCS_MIME = Gauge(
    "saga_documents_by_mime", "Documents by MIME type.", ("mime_type",), registry=SAGA_REGISTRY
)
G_FOLDERS = Gauge("saga_folders_total", "Total folders.", registry=SAGA_REGISTRY)
G_DOC_TYPES = Gauge("saga_doc_types_total", "Total doc-types.", registry=SAGA_REGISTRY)
G_EVENTS = Gauge("saga_events_total", "Events by category.", ("category",), registry=SAGA_REGISTRY)
G_SIZE_SUM = Gauge("saga_document_size_bytes_sum", "Sum of document sizes.", registry=SAGA_REGISTRY)
G_SIZE_MAX = Gauge("saga_document_size_bytes_max", "Largest document size.", registry=SAGA_REGISTRY)
G_SIZE_AVG = Gauge("saga_document_size_bytes_avg", "Average document size.", registry=SAGA_REGISTRY)
G_CHUNKS = Gauge("saga_chunks_total", "Total chunks in the vector index.", registry=SAGA_REGISTRY)
G_STORAGE = Gauge(
    "saga_storage_bytes", "Storage usage per backend.", ("backend",), registry=SAGA_REGISTRY
)
G_QUEUE_DEPTH = Gauge("saga_queue_depth", "Pending ingestion jobs.", registry=SAGA_REGISTRY)


class StatsResponse(BaseModel):
    snapshot: Snapshot
    pipeline: PipelineAggregates


router = APIRouter(tags=["statistics"], dependencies=[AuthDep])
metrics_router = APIRouter(tags=["statistics"])


@router.get("/stats", response_model=StatsResponse, summary="Archive statistics snapshot")
async def get_stats(snapshot: SnapshotDep, services: ServicesDep) -> StatsResponse:
    snap: Snapshot = await snapshot.collect()
    pipeline = await read_pipeline_aggregates(services.queue)
    return StatsResponse(snapshot=snap, pipeline=pipeline)


def _refresh_gauges(snap: Snapshot) -> None:
    c = snap.counts
    G_DOCS_TOTAL.set(c.documents_total)
    G_FOLDERS.set(c.folders_total)
    G_DOC_TYPES.set(c.doc_types_total)
    G_SIZE_SUM.set(c.size_bytes_sum)
    G_SIZE_MAX.set(c.size_bytes_max)
    G_SIZE_AVG.set(c.size_bytes_avg)
    for gauge, _mapping in (
        (G_DOCS_STATUS, c.documents_by_status),
        (G_DOCS_DOCTYPE, c.documents_by_doc_type),
        (G_DOCS_MIME, c.documents_by_mime),
        (G_EVENTS, c.events_by_category),
    ):
        gauge.clear()  # drop stale label series before re-setting
    for status, n in c.documents_by_status.items():
        G_DOCS_STATUS.labels(status=status).set(n)
    for dt, n in c.documents_by_doc_type.items():
        G_DOCS_DOCTYPE.labels(doc_type=dt).set(n)
    for mime, n in c.documents_by_mime.items():
        G_DOCS_MIME.labels(mime_type=mime).set(n)
    for cat, n in c.events_by_category.items():
        G_EVENTS.labels(category=cat).set(n)
    s = snap.storage
    G_CHUNKS.set(snap.chunks_total or 0)
    G_QUEUE_DEPTH.set(s.queue_depth or 0)
    G_STORAGE.clear()
    for backend, value in (
        ("postgres", s.postgres_bytes),
        ("opensearch", s.opensearch_bytes),
        ("minio", s.minio_bytes),
        ("redis", s.redis_bytes),
    ):
        if value is not None:
            G_STORAGE.labels(backend=backend).set(value)


@metrics_router.get("/metrics", summary="Prometheus metrics")
async def metrics(snapshot: SnapshotDep) -> Response:
    _refresh_gauges(await snapshot.collect())
    return Response(generate_latest(SAGA_REGISTRY), media_type=CONTENT_TYPE_LATEST)
