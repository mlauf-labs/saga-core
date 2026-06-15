"""Typed configuration for embedding providers (FR-33), from ``providers.yaml``."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from saga.core.config import load_yaml
from saga.core.errors import ConfigError


class EmbeddingProviderSettings(BaseModel):
    """Connection/model settings for one embedding provider (superset of fields)."""

    base_url: str | None = None
    model: str | None = None
    dimension: int = 768
    api_key: str | None = None
    endpoint: str | None = None
    api_version: str | None = None
    deployment: str | None = None


class EmbeddingsConfig(BaseModel):
    """The ``embeddings:`` section: selected provider + per-provider settings."""

    provider: str = "ollama"
    providers: dict[str, EmbeddingProviderSettings] = Field(default_factory=dict)
    # Number of texts sent per embedding request.
    batch_size: int = 32

    @property
    def active(self) -> EmbeddingProviderSettings:
        settings = self.providers.get(self.provider)
        if settings is None:
            raise ConfigError(
                f"Embedding provider '{self.provider}' is selected but not configured "
                f"under embeddings.providers in providers.yaml."
            )
        return settings


def load_embeddings_config(config_dir: Path | str = "config") -> EmbeddingsConfig:
    """Load and validate the ``embeddings:`` section of ``providers.yaml``."""
    data = load_yaml(Path(config_dir) / "providers.yaml")
    section = data.get("embeddings")
    if not isinstance(section, dict):
        raise ConfigError("providers.yaml is missing the 'embeddings' section.")
    return EmbeddingsConfig.model_validate(section)
