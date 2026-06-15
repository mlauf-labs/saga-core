"""Typed configuration for the conversion services and routing (FR-3).

Loaded from ``config/converters.yaml`` with ``${ENV}`` resolution.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from saga.converters.router import RoutingConfig
from saga.core.config import load_yaml


class OcrConfig(BaseModel):
    """OCR settings for a conversion service."""

    enabled: bool = True
    force: bool = False
    preset: str = "auto"
    languages: list[str] = Field(default_factory=list)


class VlmConfig(BaseModel):
    """VLM pipeline settings for docling-serve.

    Three mutually exclusive modes, selected by the ``mode`` field:

    * ``api``    - Send page images to an external OpenAI-compatible endpoint
                   (Ollama, vLLM, LM Studio, …). Needs ``api_url`` + ``model``.
    * ``preset`` - Use a built-in docling-serve preset; the model is loaded
                   inside the docling-serve container (no external dependency).
                   Needs ``preset``.
    * ``local``  - Download a HuggingFace model on first use and run it inline
                   inside the container (CPU/GPU via Transformers).
                   Needs ``repo_id`` and optionally the inference settings.

    ``response_format`` must match the model:
    - ``markdown``  → generic vision models (qwen2.5vl, granite-vision, …)
    - ``doctags``   → IBM granite-docling and SmolDocling
    """

    enabled: bool = False

    # Which delivery mode to use (see class docstring).
    mode: str = "api"  # "api" | "preset" | "local"

    # ── mode: api ────────────────────────────────────────────────────────────
    # URL of the OpenAI-compatible chat/completions endpoint.
    api_url: str = ""
    # Model name as understood by the endpoint (e.g. "qwen2.5vl:7b").
    model: str = ""
    # Max simultaneous image requests sent to the endpoint.
    concurrency: int = 1

    # ── mode: preset ─────────────────────────────────────────────────────────
    # Built-in preset name registered in docling-serve.
    # Common presets (docling 2.91+): granite_docling, smoldocling,
    #   granite_vision, granite_vision_ollama, granite_docling_ollama
    preset: str = "granite_docling"

    # ── mode: local ──────────────────────────────────────────────────────────
    # HuggingFace repo ID; downloaded automatically on first request.
    repo_id: str = "ibm-granite/granite-docling-258M"
    # "transformers" (CPU/CUDA/XPU) or "mlx" (Apple Silicon only).
    inference_framework: str = "transformers"
    # Transformers model class used for image-to-text inference.
    transformers_model_type: str = "automodel-imagetexttotext"
    # Maximum tokens the model may generate per page.
    max_new_tokens: int = 8192
    # Load model weights in 8-bit (saves ~50 % RAM on CPU, needs bitsandbytes).
    load_in_8bit: bool = True

    # ── shared settings (api + local modes) ──────────────────────────────────
    prompt: str = (
        "Convert this page to markdown. Extract all text and preserve tables and structure exactly."
    )
    response_format: str = "markdown"  # "markdown" | "doctags"
    timeout: float = 120.0
    scale: float = 2.0
    temperature: float = 0.0


class ServiceConfig(BaseModel):
    """Connection + behaviour settings for a single conversion service."""

    base_url: str
    timeout_seconds: float = 600.0
    output_format: str = "markdown"
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    vlm: VlmConfig = Field(default_factory=VlmConfig)
    # Optional API key (e.g. Docling ``X-Api-Key``).
    api_key: str | None = None
    # Retry policy for transient failures (NFR-14).
    max_retries: int = 3


class ConvertersConfig(BaseModel):
    """Top-level converters configuration: services + routing."""

    services: dict[str, ServiceConfig]
    routing: RoutingConfig


def load_converters_config(config_dir: Path | str = "config") -> ConvertersConfig:
    """Load and validate ``converters.yaml``."""
    data = load_yaml(Path(config_dir) / "converters.yaml")
    return ConvertersConfig.model_validate(data)
