"""Prometheus registry and cumulative metric definitions (worker + api).

A dedicated ``CollectorRegistry`` keeps SAGA metrics isolated from the global
default registry, so importing this module twice (e.g. in tests) is safe and the
``/metrics`` exposition contains only SAGA series.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

#: The fixed pipeline stage names, in execution order.
STAGE_NAMES: tuple[str, ...] = (
    "convert",
    "classify_doc_type",
    "extract_values",
    "extract_timeline",
    "summarize",
    "compute_similarity",
    "place_in_folder",
    "index_chunks",
)

#: Isolated registry holding every SAGA metric.
SAGA_REGISTRY = CollectorRegistry()

STAGE_DURATION = Histogram(
    "saga_pipeline_stage_duration_seconds",
    "Duration of a single ingestion pipeline stage.",
    labelnames=("stage",),
    registry=SAGA_REGISTRY,
)
PIPELINE_DURATION = Histogram(
    "saga_pipeline_total_duration_seconds",
    "End-to-end ingestion duration per document.",
    registry=SAGA_REGISTRY,
)
INGEST_TOTAL = Counter(
    "saga_ingest_total",
    "Completed ingestion runs by result.",
    labelnames=("result",),
    registry=SAGA_REGISTRY,
)
CONVERTER_DURATION = Histogram(
    "saga_converter_duration_seconds",
    "Document conversion duration by converter.",
    labelnames=("converter",),
    registry=SAGA_REGISTRY,
)
LLM_TOKENS = Counter(
    "saga_llm_tokens_total",
    "LLM tokens consumed by pipeline step, model, and kind (prompt/completion).",
    labelnames=("step", "model", "kind"),
    registry=SAGA_REGISTRY,
)
LLM_COST = Counter(
    "saga_llm_cost_usd_total",
    "Estimated LLM cost in USD by model.",
    labelnames=("model",),
    registry=SAGA_REGISTRY,
)
LLM_CALL_DURATION = Histogram(
    "saga_llm_call_duration_seconds",
    "LLM call latency by model.",
    labelnames=("model",),
    registry=SAGA_REGISTRY,
)
