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
from pydantic import BaseModel, Field, field_validator

from saga.core.errors import ConfigError

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
            f"provided. Set it (see .env.example) before starting Saga."
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
    # CORS for browser UIs. Default allows all origins; restrict in production.
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = True

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string (from env) or a list of origins."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


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
    hybrid_pipeline: str = "saga-hybrid"
    vector_dimension: int = 768
    vector_space_type: str = "cosinesimil"
    vector_engine: str = "faiss"
    knn_ef_construction: int = 256
    knn_m: int = 16
    hybrid_weights: list[float] = Field(default_factory=lambda: [0.4, 0.6])
    hybrid_normalization: str = "min_max"
    hybrid_combination: str = "arithmetic_mean"
    # Keyword (query_string) search over the document projection (FR-19/20).
    keyword_search_fields: list[str] = Field(
        default_factory=lambda: ["title^3", "summary^2", "content_markdown", "doc_type"]
    )
    keyword_default_operator: str = "OR"
    # Reciprocal Rank Fusion constant for combining keyword + semantic rankings (FR-19).
    rrf_k: int = 60


class MinioConfig(BaseModel):
    endpoint: str = "minio:9000"
    access_key: str = ""
    secret_key: str = ""
    secure: bool = False
    bucket: str = "saga-originals"


class PostgresConfig(BaseModel):
    """Connection settings for the relational system of record (folders, docs, notes)."""

    dsn: str = "postgresql+asyncpg://saga:saga@postgres:5432/saga"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False


class SimilarityConfig(BaseModel):
    """Weights and bounds for the ingestion-time document-similarity step (FR-16).

    The combined similarity score of a candidate document is::

        w_sem * cosine(summary) + w_lex * norm_bm25 + w_type * [same doc_type]
            + w_val * jaccard(extracted_values)
    """

    candidate_pool: int = 50
    top_k: int = 10
    weight_semantic: float = 0.6
    weight_lexical: float = 0.15
    weight_doc_type: float = 0.1
    weight_values: float = 0.15
    # Boost applied to a similar document's primary folder when voting.
    primary_folder_boost: float = 1.5
    # Fraction of a folder's vote also credited to each ancestor folder.
    ancestor_credit: float = 0.3
    # How many of the top folder votes to surface to the placement LLM.
    max_folder_votes: int = 5


class TimelineConfig(BaseModel):
    """Tunables for the timeline event-log subsystem.

    Controls how many rationale documents are fanned out per event query and
    the pagination bounds for timeline list endpoints.
    """

    # Number of top related documents to include in event rationale.
    rationale_top_n: int = 5
    # Default page size for timeline list endpoints.
    default_page_size: int = 50
    # Hard upper bound on page size to protect query performance.
    max_page_size: int = 500


class ExportConfig(BaseModel):
    """Tunables for the OKF export."""

    # Public base URL used to build resolvable OKF `resource` links
    # (e.g. https://saga.example.com). When unset, a saga:// URI is used.
    public_base_url: str | None = None


class RedisConfig(BaseModel):
    url: str = "redis://redis:6379/0"


class ChunkingConfig(BaseModel):
    max_tokens: int = 512
    overlap_tokens: int = 64
    tokenizer: str = "cl100k_base"


class DedupConfig(BaseModel):
    """Duplicate-handling behaviour (FR-13) and update semantics (FR-11)."""

    # reject | replace | allow
    on_duplicate: str = "replace"
    # keep | new
    document_id_on_update: str = "keep"


class LangfuseConfig(BaseModel):
    """Optional Langfuse observability integration (FR-33 / NFR-30).

    Tracing is enabled **only** when both ``public_key`` and ``secret_key`` are
    non-empty. When disabled every ``PipelineTracer`` call is a no-op and no
    Langfuse dependency is exercised at runtime.
    """

    public_key: str = ""
    secret_key: str = ""
    # Cloud EU: https://cloud.langfuse.com  |  US: https://us.cloud.langfuse.com
    # Self-hosted: http://your-langfuse-host
    host: str = "https://cloud.langfuse.com"

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self.secret_key)


class GenerationConfig(BaseModel):
    """Language and style settings for LLM-generated content (FR-14/16/17).

    Controls the natural language used when the LLM writes summaries, folder
    names/descriptions, and doc-type descriptions.

    **Exception**: value/metadata extraction (FR-15) always uses English
    regardless of this setting, because extracted keys (e.g. ``invoice_number``,
    ``iban``) are used as search/filter terms and must stay consistent.
    """

    # BCP-47 language tag or plain name understood by the LLM, e.g. "English",
    # "German", "French", "de", "fr-CH".  The full name ("German") is more
    # reliable with smaller local models than the tag ("de").
    language: str = "English"
    # Optional free-text description of what this archive is for. Injected into
    # LLM system prompts to guide folder names, doc-type labels, and generated
    # descriptions toward the archive's actual purpose.
    description: str = ""
    # Optional per-step custom instructions loaded from config/prompts/*.md.
    # These are appended to the corresponding LLM system prompt so operators
    # can tailor each analysis step without touching the core prompt templates.
    prompt_doctype: str = ""
    prompt_metadata: str = ""
    prompt_summary: str = ""
    prompt_folder: str = ""


class AppConfig(BaseModel):
    """Root application configuration assembled from the YAML files."""

    name: str = "saga"
    environment: str = "development"
    api: ApiConfig = Field(default_factory=ApiConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    opensearch: OpenSearchConfig = Field(default_factory=OpenSearchConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    minio: MinioConfig = Field(default_factory=MinioConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    similarity: SimilarityConfig = Field(default_factory=SimilarityConfig)
    timeline: TimelineConfig = Field(default_factory=TimelineConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)


def _load_prompt_file(path: Path) -> str:
    """Read a prompt config file and return only the active body.

    Files may use a YAML-style front-matter block (``---`` / ``---``) to hold
    documentation and usage examples without injecting them into LLM prompts.
    Only the text *after* the closing ``---`` line is returned; if there is no
    front-matter the full file content is returned.

    This allows operators to keep rich explanatory comments at the top of each
    file while the actual instructions stay clearly separated below.
    """
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :].strip()
    return text.strip()


def _resolve_prompt_config(
    config_dir: Path,
    filename: str,
    env_var_inline: str,
    env_var_file: str,
) -> str:
    """Resolve a prompt instruction string with the following priority:

    1. ``env_var_inline`` — inline value supplied directly via environment.
    2. ``env_var_file`` — explicit path to a text/Markdown file.
    3. ``config/prompts/<filename>`` — convention-based default file.

    Returns an empty string when no source provides a value.
    """
    inline = os.environ.get(env_var_inline, "").strip()
    if inline:
        return inline

    explicit = os.environ.get(env_var_file, "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"{env_var_file} points to a non-existent file: {path}")
        return _load_prompt_file(path)

    default = config_dir / "prompts" / filename
    if default.is_file():
        return _load_prompt_file(default)

    return ""


def load_config(config_dir: Path | str = "config") -> AppConfig:
    """Load and validate the application configuration from ``config/``."""
    base = Path(config_dir)
    data = load_yaml(base / "config.yaml")
    merged: dict[str, Any] = {}
    merged.update(data.get("app", {}))
    for key in (
        "api",
        "mcp",
        "security",
        "opensearch",
        "postgres",
        "minio",
        "redis",
        "chunking",
        "dedup",
        "similarity",
        "timeline",
        "export",
        "generation",
        "langfuse",
    ):
        if key in data:
            merged[key] = data[key]
    config = AppConfig.model_validate(merged)

    # Load prompt config files from config/prompts/ when not supplied inline.
    # Each field checks its own env var first so individual steps can be
    # overridden independently without touching the files.
    if not config.generation.description:
        config.generation.description = _resolve_prompt_config(
            base,
            "store-description.md",
            "SAGA_STORE_DESCRIPTION",
            "SAGA_STORE_DESCRIPTION_FILE",
        )
    if not config.generation.prompt_doctype:
        config.generation.prompt_doctype = _resolve_prompt_config(
            base,
            "doctype.md",
            "SAGA_PROMPT_DOCTYPE",
            "SAGA_PROMPT_DOCTYPE_FILE",
        )
    if not config.generation.prompt_metadata:
        config.generation.prompt_metadata = _resolve_prompt_config(
            base,
            "metadata.md",
            "SAGA_PROMPT_METADATA",
            "SAGA_PROMPT_METADATA_FILE",
        )
    if not config.generation.prompt_summary:
        config.generation.prompt_summary = _resolve_prompt_config(
            base,
            "summary.md",
            "SAGA_PROMPT_SUMMARY",
            "SAGA_PROMPT_SUMMARY_FILE",
        )
    if not config.generation.prompt_folder:
        config.generation.prompt_folder = _resolve_prompt_config(
            base,
            "folder.md",
            "SAGA_PROMPT_FOLDER",
            "SAGA_PROMPT_FOLDER_FILE",
        )

    return config
