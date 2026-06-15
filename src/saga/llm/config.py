"""Typed configuration for LLM providers (FR-33), loaded from ``providers.yaml``.

A single settings model carries the union of fields used by the Ollama, OpenAI and
Azure adapters; each adapter validates the fields it requires and raises an
actionable :class:`ConfigError` when one is missing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

from saga.core.config import load_yaml
from saga.core.errors import ConfigError


def _empty_str_to_none(v: Any) -> Any:  # noqa: ANN401
    """Coerce an empty / whitespace-only string to None (for env-var placeholders)."""
    if isinstance(v, str) and not v.strip():
        return None
    return v


_OptionalInt = Annotated[int | None, BeforeValidator(_empty_str_to_none)]


#: Names of the four LLM-driven pipeline steps that support per-step model overrides.
PIPELINE_STEPS = ("doc_type", "value_extraction", "summary", "folder_placement")


class LlmProviderSettings(BaseModel):
    """Connection/model settings for one LLM provider (superset of fields)."""

    # Ollama / OpenAI
    base_url: str | None = None
    model: str | None = None
    temperature: float = 0.0
    # Maximum tokens to generate per call.  None (or empty env var) = no limit.
    max_output_tokens: _OptionalInt = None
    # Per-request timeout in seconds.
    # With streaming=True this is the maximum allowed silence *between two consecutive
    # stream chunks* (i.e. between tokens), not the total generation time.  The
    # connection stays open as long as tokens keep arriving, which means slow local
    # models can take as long as they need without hitting the timeout.
    request_timeout: float = 300.0
    # Enable HTTP response streaming.  When True, LangChain's ainvoke() uses the
    # SSE streaming endpoint and accumulates chunks; each received token resets
    # httpx's read-timeout so the connection cannot be killed mid-generation by an
    # idle timeout.  Disable only if your proxy/load-balancer does not support SSE.
    streaming: bool = True
    # OpenAI / Azure
    api_key: str | None = None
    # Azure
    endpoint: str | None = None
    api_version: str | None = None
    deployment: str | None = None


class DocTypeClassificationConfig(BaseModel):
    """Behaviour of the doc-type classification step (FR-14)."""

    # Allow the LLM to coin a brand-new doc-type when none of the existing fit.
    allow_auto_create: bool = True
    # How many existing doc-types (name + description) to show the model as context.
    max_doctypes_in_prompt: int = 100


class FolderPlacementConfig(BaseModel):
    """Behaviour of the LLM folder-placement step (FR-16/17)."""

    # Allow the LLM to create new folders when no existing folder fits well.
    allow_auto_create: bool = True
    # How many existing folders to render into the placement prompt's tree.
    max_folders_in_prompt: int = 200


class PipelineStepModelConfig(BaseModel):
    """Optional model/fallback override for a single pipeline step.

    Empty or absent values mean the step inherits the global provider model.
    """

    model: str | None = None
    fallback_model: str | None = None


class PipelineStepsConfig(BaseModel):
    """Per-step model overrides for the four LLM-driven pipeline steps.

    Each field defaults to an empty :class:`PipelineStepModelConfig`, which means
    "no override — use the global provider model".
    """

    doc_type: PipelineStepModelConfig = Field(default_factory=PipelineStepModelConfig)
    value_extraction: PipelineStepModelConfig = Field(default_factory=PipelineStepModelConfig)
    summary: PipelineStepModelConfig = Field(default_factory=PipelineStepModelConfig)
    folder_placement: PipelineStepModelConfig = Field(default_factory=PipelineStepModelConfig)


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
    doctype_classification: DocTypeClassificationConfig = Field(
        default_factory=DocTypeClassificationConfig
    )
    folder_placement: FolderPlacementConfig = Field(default_factory=FolderPlacementConfig)
    steps: PipelineStepsConfig = Field(default_factory=PipelineStepsConfig)

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

    def step_active(self, step: str) -> LlmProviderSettings:
        """Active settings for a pipeline step.

        Returns the global :attr:`active` settings with the model name overridden when
        the step has an explicit ``model`` configured; otherwise returns :attr:`active`
        unchanged.
        """
        step_cfg: PipelineStepModelConfig = getattr(self.steps, step, PipelineStepModelConfig())
        if step_cfg.model:
            return self.active.model_copy(update={"model": step_cfg.model})
        return self.active

    def step_fallback(self, step: str) -> LlmProviderSettings | None:
        """Fallback settings for a pipeline step.

        Returns step-level fallback settings when the step has an explicit
        ``fallback_model`` configured; otherwise falls back to the global
        :attr:`fallback` (which may itself be ``None``).
        """
        step_cfg: PipelineStepModelConfig = getattr(self.steps, step, PipelineStepModelConfig())
        if step_cfg.fallback_model:
            return self.active.model_copy(update={"model": step_cfg.fallback_model})
        return self.fallback

    def has_step_model_override(self, step: str) -> bool:
        """Return ``True`` when the step has any model or fallback_model override."""
        step_cfg: PipelineStepModelConfig = getattr(self.steps, step, PipelineStepModelConfig())
        return bool(step_cfg.model) or bool(step_cfg.fallback_model)


def load_llm_config(config_dir: Path | str = "config") -> LlmConfig:
    """Load and validate the ``llm:`` section of ``providers.yaml``."""
    data = load_yaml(Path(config_dir) / "providers.yaml")
    llm_section = data.get("llm")
    if not isinstance(llm_section, dict):
        raise ConfigError("providers.yaml is missing the 'llm' section.")
    return LlmConfig.model_validate(llm_section)
