from __future__ import annotations

import fakeredis.aioredis

from saga.metrics.redis_aggregate import (
    read_pipeline_aggregates,
    record_ingest,
    record_stage,
    record_tokens,
)


async def test_stage_aggregate_tracks_min_max_avg() -> None:
    r = fakeredis.aioredis.FakeRedis()
    await record_stage(r, "convert", 100.0)
    await record_stage(r, "convert", 300.0)
    agg = await read_pipeline_aggregates(r)
    s = agg.stages["convert"]
    assert s.count == 2
    assert s.min_ms == 100.0
    assert s.max_ms == 300.0
    assert s.avg_ms == 200.0


async def test_tokens_and_ingest() -> None:
    r = fakeredis.aioredis.FakeRedis()
    await record_tokens(r, "summarize", "m", prompt=10, completion=5)
    await record_tokens(r, "summarize", "m", prompt=2, completion=0)
    await record_ingest(r, "success")
    await record_ingest(r, "failed")
    agg = await read_pipeline_aggregates(r)
    prompt = next(t for t in agg.tokens if t.kind == "prompt")
    assert prompt.tokens == 12
    assert agg.ingest == {"success": 1, "failed": 1}
