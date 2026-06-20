"""LangChain callback that records LLM token usage to Prometheus + Redis."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import AsyncCallbackHandler

from saga.core.logging import get_logger
from saga.metrics.redis_aggregate import record_tokens
from saga.metrics.registry import LLM_COST, LLM_TOKENS

if TYPE_CHECKING:
    from uuid import UUID

    from saga.core.config import ModelPrice

_log = get_logger("saga.metrics.callbacks")
_UNKNOWN = "unknown"


def _extract_usage(response: Any) -> tuple[int, int]:  # noqa: ANN401
    """Return (prompt, completion) tokens from an LLMResult-like response."""
    try:
        for batch in getattr(response, "generations", []) or []:
            for gen in batch:
                usage = getattr(getattr(gen, "message", None), "usage_metadata", None)
                if usage:
                    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    except Exception:  # never break on shape differences
        return 0, 0


class PrometheusTokenCallback(AsyncCallbackHandler):
    """Records prompt/completion tokens (and optional cost) per (step, model)."""

    def __init__(
        self,
        step: str,
        redis: Any,  # noqa: ANN401
        prices: dict[str, ModelPrice] | None = None,
    ) -> None:
        self._step = step
        self._redis = redis
        self._prices = prices or {}
        self._models: dict[UUID, str] = {}

    def _model_from(self, serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
        ser = serialized or {}
        params = kwargs.get("invocation_params") or ser.get("invocation_params") or {}
        metadata = kwargs.get("metadata") or {}
        return str(
            params.get("model")
            or params.get("model_name")
            or metadata.get("ls_model_name")
            or ser.get("name")
            or _UNKNOWN
        )

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        self._models[run_id] = self._model_from(serialized, kwargs)

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,  # noqa: ANN401
        *,
        run_id: UUID,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        self._models[run_id] = self._model_from(serialized, kwargs)

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ANN401
        model = self._models.pop(run_id, _UNKNOWN)
        prompt, completion = _extract_usage(response)
        if not prompt and not completion:
            return
        if prompt:
            LLM_TOKENS.labels(step=self._step, model=model, kind="prompt").inc(prompt)
        if completion:
            LLM_TOKENS.labels(step=self._step, model=model, kind="completion").inc(completion)
        price = self._prices.get(model)
        if price is not None:
            cost = prompt / 1000 * price.prompt_per_1k + completion / 1000 * price.completion_per_1k
            if cost:
                LLM_COST.labels(model=model).inc(cost)
        await record_tokens(self._redis, self._step, model, prompt, completion)
