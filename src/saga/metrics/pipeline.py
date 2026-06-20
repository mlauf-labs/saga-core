"""Pipeline instrumentation: time a stage (Prometheus + Redis) and inject the token callback."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

from saga.metrics.callbacks import PrometheusTokenCallback
from saga.metrics.redis_aggregate import record_ingest, record_stage
from saga.metrics.registry import INGEST_TOTAL, STAGE_DURATION

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from saga.core.config import ModelPrice
    from saga.llm.tracing import _BaseTracer


@contextlib.asynccontextmanager
async def instrumented_stage(
    tracer: _BaseTracer,
    redis: Any,  # noqa: ANN401 - arq ArqRedis
    stage: str,
    *,
    prices: dict[str, ModelPrice] | None = None,
    input: dict[str, Any] | None = None,  # matches tracer.step_span kwarg name
) -> AsyncIterator[list[Any]]:
    """Wrap a pipeline stage: open the tracer span, time it, and yield callbacks.

    Yields the tracer's LangChain callbacks plus a :class:`PrometheusTokenCallback`
    bound to ``stage``. On exit, records the stage duration to the Prometheus
    histogram and the Redis aggregate (best-effort).
    """
    start = time.perf_counter()
    with tracer.step_span(stage, input=input) as trace_callbacks:
        callbacks = [*trace_callbacks, PrometheusTokenCallback(stage, redis, prices)]
        try:
            yield callbacks
        finally:
            elapsed = time.perf_counter() - start
            STAGE_DURATION.labels(stage=stage).observe(elapsed)
            await record_stage(redis, stage, elapsed * 1000.0)


async def record_ingest_result(redis: Any, result: str) -> None:  # noqa: ANN401
    """Increment the ingest result counter (Prometheus + Redis)."""
    INGEST_TOTAL.labels(result=result).inc()
    await record_ingest(redis, result)
