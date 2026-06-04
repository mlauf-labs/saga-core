"""Typed configuration for LLM providers (FR-33), loaded from ``providers.yaml``.

A single settings model carries the union of fields used by the Ollama, OpenAI and
Azure adapters; each adapter validates the fields it requires and raises an
actionable :class:`ConfigError` when one is missing.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from docstore.core.config import load_yaml
from docstore.core.errors import ConfigError


class LlmProviderSettings(BaseModel):
    """Connection/model settings for one LLM provider (superset of fields)."""

    # Ollama / OpenAI
    base_url: str | None = None
    model: str | None = None
    temperature: float = 0.0
    # Cap generated tokens so a rambling model can't run unbounded (NFR-10/15).
    max_output_tokens: int = 1024
    # Per-request timeout in seconds; a stuck generation fails fast and is retried.
    request_timeout: float = 120.0
    # OpenAI / Azure
    api_key: str | None = None
    # Azure
    endpoint: str | None = None
    api_version: str | None = None
    deployment: str | None = None


class LlmConfig(BaseModel):
    """The ``llm:`` section: selected provider + per-provider settings."""

    provider: str = "ollama"
    providers: dict[str, LlmProviderSettings] = Field(default_factory=dict)
    # Maximum characters of document text sent to the LLM per analysis call.
    max_input_chars: int = 12000
    # Optional fallback model (same provider/endpoint) used by the structured-output
    # library when the primary model exhausts its validation retries.
    fallback_model: str | None = None
    # Validation-retry budgets for structured extraction (retry on schema/type errors).
    max_primary_retries: int = 3
    max_fallback_retries: int = 3

    @property
    def active(self) -> LlmProviderSettings:
        settings = self.providers.get(self.provider)
        if settings is None:
            raise ConfigError(
                f"LLM provider '{self.provider}' is selected but not configured under "
                f"llm.providers in providers.yaml."
            )
        return settings

    @property
    def fallback(self) -> LlmProviderSettings | None:
        """Settings for the fallback model, or ``None`` when not configured."""
        if not self.fallback_model:
            return None
        return self.active.model_copy(update={"model": self.fallback_model})


def load_llm_config(config_dir: Path | str = "config") -> LlmConfig:
    """Load and validate the ``llm:`` section of ``providers.yaml``."""
    data = load_yaml(Path(config_dir) / "providers.yaml")
    llm_section = data.get("llm")
    if not isinstance(llm_section, dict):
        raise ConfigError("providers.yaml is missing the 'llm' section.")
    return LlmConfig.model_validate(llm_section)
