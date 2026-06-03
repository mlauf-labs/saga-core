"""Typed configuration for the conversion services and routing (FR-3).

Loaded from ``config/converters.yaml`` with ``${ENV}`` resolution.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from docstore.converters.router import RoutingConfig
from docstore.core.config import load_yaml


class OcrConfig(BaseModel):
    """OCR settings for a conversion service."""

    enabled: bool = True
    languages: list[str] = Field(default_factory=list)


class ServiceConfig(BaseModel):
    """Connection + behaviour settings for a single conversion service."""

    base_url: str
    timeout_seconds: float = 600.0
    output_format: str = "markdown"
    ocr: OcrConfig = Field(default_factory=OcrConfig)
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
