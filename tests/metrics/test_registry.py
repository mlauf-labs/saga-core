from __future__ import annotations

from prometheus_client import generate_latest

from saga.metrics.registry import (
    INGEST_TOTAL,
    LLM_TOKENS,
    SAGA_REGISTRY,
    STAGE_DURATION,
    STAGE_NAMES,
)


def test_stage_names_cover_pipeline() -> None:
    assert "convert" in STAGE_NAMES
    assert "index_chunks" in STAGE_NAMES
    assert len(STAGE_NAMES) == 8


def test_metrics_are_registered_and_emit() -> None:
    STAGE_DURATION.labels(stage="convert").observe(0.5)
    INGEST_TOTAL.labels(result="success").inc()
    LLM_TOKENS.labels(step="summarize", model="m", kind="prompt").inc(10)
    text = generate_latest(SAGA_REGISTRY).decode()
    assert "saga_pipeline_stage_duration_seconds" in text
    assert "saga_ingest_total" in text
    assert "saga_llm_tokens_total" in text
