"""Compact 'current' pipeline aggregates in Redis, read by /stats (best-effort)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from saga.core.logging import get_logger
from saga.metrics.registry import STAGE_NAMES

_log = get_logger("saga.metrics.redis_aggregate")

_TOKENS_KEY = "saga:metrics:tokens"
_INGEST_KEY = "saga:metrics:ingest"


def _stage_key(stage: str) -> str:
    return f"saga:metrics:stage:{stage}"


class StageAggregate(BaseModel):
    count: int = 0
    avg_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0


class TokenAggregate(BaseModel):
    step: str
    model: str
    kind: str
    tokens: int


class PipelineAggregates(BaseModel):
    stages: dict[str, StageAggregate] = {}
    tokens: list[TokenAggregate] = []
    ingest: dict[str, int] = {}


async def record_stage(redis: Any, stage: str, duration_ms: float) -> None:  # noqa: ANN401
    """Update count/sum/min/max for a stage (best-effort read-modify-write)."""
    try:
        key = _stage_key(stage)
        await redis.hincrby(key, "count", 1)
        await redis.hincrbyfloat(key, "sum_ms", duration_ms)
        current = await redis.hmget(key, "min_ms", "max_ms")
        cur_min = current[0]
        cur_max = current[1]
        if cur_min is None or duration_ms < float(cur_min):
            await redis.hset(key, "min_ms", duration_ms)
        if cur_max is None or duration_ms > float(cur_max):
            await redis.hset(key, "max_ms", duration_ms)
    except Exception as exc:  # never break ingestion
        _log.warning("record_stage_failed", stage=stage, error=str(exc))


async def record_tokens(redis: Any, step: str, model: str, prompt: int, completion: int) -> None:  # noqa: ANN401
    """Accumulate prompt and completion token counts for a step/model pair (best-effort)."""
    try:
        if prompt:
            await redis.hincrby(_TOKENS_KEY, f"{step}|{model}|prompt", prompt)
        if completion:
            await redis.hincrby(_TOKENS_KEY, f"{step}|{model}|completion", completion)
    except Exception as exc:
        _log.warning("record_tokens_failed", step=step, model=model, error=str(exc))


async def record_ingest(redis: Any, result: str) -> None:  # noqa: ANN401
    """Increment ingest result counter (best-effort)."""
    try:
        await redis.hincrby(_INGEST_KEY, result, 1)
    except Exception as exc:
        _log.warning("record_ingest_failed", result=result, error=str(exc))


def _to_int(value: Any) -> int:  # noqa: ANN401
    return int(value) if value is not None else 0


def _to_float(value: Any) -> float:  # noqa: ANN401
    return float(value) if value is not None else 0.0


async def read_pipeline_aggregates(redis: Any) -> PipelineAggregates:  # noqa: ANN401
    """Read all stage/token/ingest aggregates back into a typed model."""
    try:
        stages: dict[str, StageAggregate] = {}
        for stage in STAGE_NAMES:
            raw = await redis.hgetall(_stage_key(stage))
            data = {(k.decode() if isinstance(k, bytes) else k): v for k, v in (raw or {}).items()}
            count = _to_int(data.get("count"))
            if count == 0:
                continue
            total = _to_float(data.get("sum_ms"))
            stages[stage] = StageAggregate(
                count=count,
                avg_ms=total / count if count else 0.0,
                min_ms=_to_float(data.get("min_ms")),
                max_ms=_to_float(data.get("max_ms")),
            )
        tokens_raw = await redis.hgetall(_TOKENS_KEY)
        tokens: list[TokenAggregate] = []
        for field, value in (tokens_raw or {}).items():
            field_str = field.decode() if isinstance(field, bytes) else field
            step, model, kind = field_str.split("|", 2)
            tokens.append(TokenAggregate(step=step, model=model, kind=kind, tokens=_to_int(value)))
        ingest_raw = await redis.hgetall(_INGEST_KEY)
        ingest = {
            (k.decode() if isinstance(k, bytes) else k): _to_int(v)
            for k, v in (ingest_raw or {}).items()
        }
        return PipelineAggregates(stages=stages, tokens=tokens, ingest=ingest)
    except Exception as exc:
        _log.warning("read_pipeline_aggregates_failed", error=str(exc))
        return PipelineAggregates()
