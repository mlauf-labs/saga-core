"""Typed configuration loading (NFR-9).

Loads the YAML configuration files from ``config/`` and resolves ``${ENV_VAR}``
and ``${ENV_VAR:-default}`` placeholders from the environment. Secrets are never
hard-coded; they are injected via environment variables (NFR-19).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from docstore.core.errors import ConfigError

_ENV_PATTERN = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def _resolve_env(value: str) -> str:
    """Replace ``${VAR}`` / ``${VAR:-default}`` placeholders with env values."""

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        default = match.group("default")
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        if default is not None:
            return default
        raise ConfigError(
            f"Required environment variable '{name}' is not set and no default was "
            f"provided. Set it (see .env.example) before starting DocStore."
        )

    return _ENV_PATTERN.sub(_replace, value)


def _resolve_tree(node: Any) -> Any:  # noqa: ANN401 - recursive YAML structure
    if isinstance(node, dict):
        return {key: _resolve_tree(val) for key, val in node.items()}
    if isinstance(node, list):
        return [_resolve_tree(item) for item in node]
    if isinstance(node, str):
        return _resolve_env(node)
    return node


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and resolve environment placeholders."""
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"Failed to parse YAML config '{path}': {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Top-level YAML in '{path}' must be a mapping.")
    resolved: dict[str, Any] = _resolve_tree(raw)
    return resolved


class PaginationConfig(BaseModel):
    default_page_size: int = 25
    max_page_size: int = 200


class ApiConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    enable_swagger: bool = True
    max_upload_bytes: int = 100 * 1024 * 1024
    pagination: PaginationConfig = Field(default_factory=PaginationConfig)


class McpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8100
    transport: str = "streamable-http"
    default_top_k: int = 10
    max_top_k: int = 50


class SecurityConfig(BaseModel):
    bearer_tokens: str = ""

    @property
    def tokens(self) -> list[str]:
        return [t.strip() for t in self.bearer_tokens.split(",") if t.strip()]


class OpenSearchConfig(BaseModel):
    hosts: str = "http://opensearch:9200"
    username: str = "admin"
    password: str = ""
    verify_certs: bool = False
    document_index: str = "documents"
    chunk_index: str = "document_chunks"
    hybrid_pipeline: str = "docstore-hybrid"
    vector_dimension: int = 768
    vector_space_type: str = "cosinesimil"
    vector_engine: str = "faiss"
    knn_ef_construction: int = 256
    knn_m: int = 16
    hybrid_weights: list[float] = Field(default_factory=lambda: [0.4, 0.6])
    hybrid_normalization: str = "min_max"
    hybrid_combination: str = "arithmetic_mean"


class MinioConfig(BaseModel):
    endpoint: str = "minio:9000"
    access_key: str = ""
    secret_key: str = ""
    secure: bool = False
    bucket: str = "docstore-originals"


class RedisConfig(BaseModel):
    url: str = "redis://redis:6379/0"


class ChunkingConfig(BaseModel):
    max_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class AppConfig(BaseModel):
    """Root application configuration assembled from the YAML files."""

    name: str = "docstore"
    environment: str = "development"
    api: ApiConfig = Field(default_factory=ApiConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    opensearch: OpenSearchConfig = Field(default_factory=OpenSearchConfig)
    minio: MinioConfig = Field(default_factory=MinioConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)


def load_config(config_dir: Path | str = "config") -> AppConfig:
    """Load and validate the application configuration from ``config/``."""
    base = Path(config_dir)
    data = load_yaml(base / "config.yaml")
    merged: dict[str, Any] = {}
    merged.update(data.get("app", {}))
    for key in ("api", "mcp", "security", "opensearch", "minio", "redis", "chunking"):
        if key in data:
            merged[key] = data[key]
    return AppConfig.model_validate(merged)
