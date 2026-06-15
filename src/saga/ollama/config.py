"""Typed configuration for the multi-server Ollama runtime (``ollama:`` section).

The optional top-level ``ollama:`` section of ``providers.yaml`` describes the
shared Ollama server fleet used by both the LLM and the embeddings provider when
their ``provider`` is ``ollama``. When the section (or its ``servers`` list) is
absent, the single ``base_url`` of the provider sections is used — exactly the
single-server behaviour that existed before multi-server support.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

from saga.core.config import load_yaml


def _empty_str_to_none(v: Any) -> Any:  # noqa: ANN401
    """Coerce an empty / whitespace-only string to None (for env-var placeholders)."""
    if isinstance(v, str) and not v.strip():
        return None
    return v


_OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]
_OptionalStr = Annotated[str | None, BeforeValidator(_empty_str_to_none)]

#: Default Ollama endpoint, matching the providers.yaml fallback.
DEFAULT_OLLAMA_URL = "http://ollama:11434"


class OllamaServerConfig(BaseModel):
    """One server slot of the ``ollama.servers`` list."""

    # Empty url (unfilled env slot) marks the entry as unused.
    url: _OptionalStr = None
    # Hard cap of simultaneous requests for this server. None = unlimited;
    # least-busy scheduling still steers load away from busy servers.
    max_concurrent: _OptionalInt = None


class OllamaRuntimeConfig(BaseModel):
    """The optional top-level ``ollama:`` section of ``providers.yaml``."""

    servers: list[OllamaServerConfig] = Field(default_factory=list)
    # TCP connect timeout per server attempt; this is what makes failover from a
    # powered-off host fast (the per-request read timeout stays much larger).
    connect_timeout: float = 5.0
    # Seconds a failed server is skipped before being probed again.
    cooldown_seconds: float = 30.0
    # Verify required models on every server at startup.
    startup_model_check: bool = True
    # Automatically `ollama pull` missing models (worker startup only).
    pull_missing_models: bool = True

    def resolve_servers(self, fallback_base_url: str | None) -> tuple[OllamaServerConfig, ...]:
        """The effective server fleet: filled slots, deduplicated, in order.

        Falls back to a single server built from ``fallback_base_url`` (the
        provider section's ``base_url``) when no slot is filled.
        """
        seen: dict[str, OllamaServerConfig] = {}
        for server in self.servers:
            if not server.url:
                continue
            url = server.url.strip().rstrip("/")
            if url and url not in seen:
                seen[url] = OllamaServerConfig(url=url, max_concurrent=server.max_concurrent)
        if seen:
            return tuple(seen.values())
        url = (fallback_base_url or DEFAULT_OLLAMA_URL).strip().rstrip("/")
        return (OllamaServerConfig(url=url),)


@lru_cache(maxsize=4)
def load_ollama_runtime_config(config_dir: Path | str = "config") -> OllamaRuntimeConfig:
    """Load the optional ``ollama:`` section of ``providers.yaml`` (defaults when absent).

    The whole section — and the file itself — is optional: without it the
    runtime behaves exactly like the previous single-server setup. Cached
    because it is consulted from every chat-model/embeddings builder.
    """
    path = Path(config_dir) / "providers.yaml"
    if not path.is_file():
        return OllamaRuntimeConfig()
    data = load_yaml(path)
    section = data.get("ollama")
    if not isinstance(section, dict):
        return OllamaRuntimeConfig()
    return OllamaRuntimeConfig.model_validate(section)
