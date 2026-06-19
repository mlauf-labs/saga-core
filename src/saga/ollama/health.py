"""Startup model check for the Ollama server fleet.

Collects every model name the configuration requires (LLM, fallback, per-step
overrides, embeddings) and verifies each server of the fleet has them, pulling
missing ones when requested. Unreachable servers are skipped with a warning —
a fleet member being switched off is the normal situation this feature exists
for and must never prevent the document store from starting.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

import httpx

from saga.core.logging import get_logger
from saga.llm.config import PIPELINE_STEPS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.converters.config import ConvertersConfig
    from saga.embeddings.config import EmbeddingsConfig
    from saga.llm.config import LlmConfig
    from saga.ollama.config import OllamaRuntimeConfig

_log = get_logger("saga.ollama.health")

#: Seconds between pull-progress log lines per model (downloads are chatty).
_PULL_LOG_INTERVAL = 10.0


def _normalize(name: str) -> str:
    """Ollama lists models with an explicit tag (``nomic-embed-text:latest``)."""
    return name if ":" in name else f"{name}:latest"


def resolve_check_urls(
    llm_config: LlmConfig | None,
    embeddings_config: EmbeddingsConfig | None,
    runtime: OllamaRuntimeConfig,
) -> tuple[str, ...]:
    """The Ollama servers the startup check must visit (deduplicated, in order).

    Both provider sections share the same fleet config; their ``base_url`` only
    matters as the single-server fallback when no fleet is configured.
    """
    urls: dict[str, None] = {}
    if llm_config is not None and llm_config.provider == "ollama":
        llm_settings = llm_config.providers.get("ollama")
        base_url = llm_settings.base_url if llm_settings is not None else None
        for server in runtime.resolve_servers(base_url):
            urls.setdefault(server.url or "", None)
    if embeddings_config is not None and embeddings_config.provider == "ollama":
        emb_settings = embeddings_config.providers.get("ollama")
        base_url = emb_settings.base_url if emb_settings is not None else None
        for server in runtime.resolve_servers(base_url):
            urls.setdefault(server.url or "", None)
    return tuple(url for url in urls if url)


def collect_required_ollama_models(
    llm_config: LlmConfig | None,
    embeddings_config: EmbeddingsConfig | None,
    converters_config: ConvertersConfig | None = None,
) -> set[str]:
    """All model names that must exist on every Ollama server, from config.

    Only sections whose ``provider`` is ``ollama`` contribute; OpenAI/Azure
    deployments are not Ollama models. A conversion service's **VLM model** is
    included when its VLM uses the ``api`` mode (Docling sends page images to the
    Ollama fleet) — otherwise the model would be missing and PDF conversion would
    fail at upload time.
    """
    models: set[str] = set()
    if llm_config is not None and llm_config.provider == "ollama":
        llm_settings = llm_config.providers.get("ollama")
        if llm_settings is not None and llm_settings.model:
            models.add(llm_settings.model)
        if llm_config.fallback_model:
            models.add(llm_config.fallback_model)
        for step in PIPELINE_STEPS:
            step_cfg = getattr(llm_config.steps, step, None)
            if step_cfg is None:
                continue
            if step_cfg.model:
                models.add(step_cfg.model)
            if step_cfg.fallback_model:
                models.add(step_cfg.fallback_model)
    if embeddings_config is not None and embeddings_config.provider == "ollama":
        emb_settings = embeddings_config.providers.get("ollama")
        if emb_settings is not None and emb_settings.model:
            models.add(emb_settings.model)
    if converters_config is not None:
        for service in converters_config.services.values():
            vlm = service.vlm
            if vlm.enabled and vlm.mode == "api" and vlm.model:
                models.add(vlm.model)
    return models


async def ensure_ollama_models(
    urls: Sequence[str],
    models: set[str],
    *,
    pull_missing: bool = True,
    connect_timeout: float = 5.0,
) -> dict[str, list[str]]:
    """Check (and optionally pull) the required models on every server.

    Returns ``{url: still_missing_models}`` — unreachable servers report all
    models as missing. Never raises for server-side problems; startup must
    survive a switched-off fleet member or a typo'd model name.
    """
    if not urls or not models:
        return {url: [] for url in urls}
    results = await asyncio.gather(
        *(
            _ensure_one(url, models, pull_missing=pull_missing, connect_timeout=connect_timeout)
            for url in urls
        )
    )
    return dict(zip(urls, results, strict=True))


async def _ensure_one(
    url: str, models: set[str], *, pull_missing: bool, connect_timeout: float
) -> list[str]:
    from ollama import AsyncClient, ResponseError

    client = AsyncClient(
        host=url,
        # Long read timeout for pulls (progress chunks keep it alive); short
        # connect timeout so an offline machine is skipped within seconds.
        timeout=httpx.Timeout(300.0, connect=connect_timeout),
    )
    try:
        try:
            listing = await client.list()
        except (httpx.HTTPError, ResponseError, OSError) as exc:
            _log.warning(
                "ollama_server_unreachable",
                url=url,
                error=str(exc),
                hint="Server may be switched off; it will be used when it comes back.",
            )
            return sorted(models)

        present = {_normalize(m.model) for m in listing.models if m.model}
        missing = sorted(m for m in models if _normalize(m) not in present)
        if not missing:
            _log.info("ollama_models_ok", url=url, models=sorted(models))
            return []
        _log.warning("ollama_models_missing", url=url, missing=missing)
        if not pull_missing:
            return missing

        still_missing: list[str] = []
        for model in missing:
            if await _pull_model(client, url, model):
                continue
            still_missing.append(model)
        return still_missing
    finally:
        with contextlib.suppress(Exception):
            await client._client.aclose()


async def _pull_model(client: object, url: str, model: str) -> bool:
    """Pull one model with throttled progress logging; False on failure."""
    from ollama import ResponseError

    _log.info("ollama_model_pull_started", url=url, model=model)
    last_logged = time.monotonic()
    try:
        async for part in await client.pull(model, stream=True):  # type: ignore[attr-defined]
            now = time.monotonic()
            if now - last_logged >= _PULL_LOG_INTERVAL:
                last_logged = now
                _log.info(
                    "ollama_model_pull_progress",
                    url=url,
                    model=model,
                    status=part.status,
                    completed=part.completed,
                    total=part.total,
                )
    except (httpx.HTTPError, ResponseError, OSError) as exc:
        _log.error("ollama_model_pull_failed", url=url, model=model, error=str(exc))
        return False
    _log.info("ollama_model_pull_done", url=url, model=model)
    return True
