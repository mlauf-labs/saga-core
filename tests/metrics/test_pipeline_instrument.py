# saga-core/tests/metrics/test_pipeline_instrument.py
from __future__ import annotations

import fakeredis.aioredis

from saga.llm.tracing import noop_tracer
from saga.metrics.callbacks import PrometheusTokenCallback
from saga.metrics.pipeline import instrumented_stage, record_ingest_result
from saga.metrics.redis_aggregate import read_pipeline_aggregates
from saga.metrics.registry import SAGA_REGISTRY
from prometheus_client import generate_latest


async def test_instrumented_stage_times_and_yields_callbacks() -> None:
    r = fakeredis.aioredis.FakeRedis()
    tracer = noop_tracer()
    async with instrumented_stage(tracer, r, "convert") as callbacks:
        assert any(isinstance(cb, PrometheusTokenCallback) for cb in callbacks)
    agg = await read_pipeline_aggregates(r)
    assert agg.stages["convert"].count == 1
    assert "saga_pipeline_stage_duration_seconds" in generate_latest(SAGA_REGISTRY).decode()


async def test_record_ingest_result() -> None:
    r = fakeredis.aioredis.FakeRedis()
    await record_ingest_result(r, "success")
    agg = await read_pipeline_aggregates(r)
    assert agg.ingest["success"] == 1
