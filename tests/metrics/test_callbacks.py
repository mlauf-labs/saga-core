# tests/metrics/test_callbacks.py
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
from prometheus_client import generate_latest

from saga.core.config import ModelPrice
from saga.metrics.callbacks import PrometheusTokenCallback
from saga.metrics.registry import SAGA_REGISTRY


async def test_callback_records_tokens_and_cost() -> None:
    r = fakeredis.aioredis.FakeRedis()
    prices = {"m": ModelPrice(prompt_per_1k=1.0, completion_per_1k=2.0)}
    cb = PrometheusTokenCallback("summarize", r, prices=prices)
    run_id = uuid4()
    await cb.on_llm_start({"invocation_params": {"model": "m"}}, ["hi"], run_id=run_id)
    # LLMResult-like object with usage on the generation message.
    usage = {"input_tokens": 1000, "output_tokens": 500}
    gen = SimpleNamespace(message=SimpleNamespace(usage_metadata=usage))
    result = SimpleNamespace(generations=[[gen]], llm_output=None)
    await cb.on_llm_end(result, run_id=run_id)

    text = generate_latest(SAGA_REGISTRY).decode()
    assert 'saga_llm_tokens_total{kind="prompt",model="m",step="summarize"} 1000.0' in text
    # cost = 1000/1000*1.0 + 500/1000*2.0 = 2.0
    assert 'saga_llm_cost_usd_total{model="m"} 2.0' in text
