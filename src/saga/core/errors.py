"""Exception hierarchy with actionable error messages (NFR-15).

Every exception carries a human-readable message describing *what* failed and,
where possible, *how* to fix it. API/MCP layers map these to appropriate
responses.
"""

from __future__ import annotations


class SagaError(Exception):
    """Base class for all Saga errors."""

    #: Stable, machine-readable error code surfaced to API clients.
    code: str = "saga_error"


class ConfigError(SagaError):
    """Raised when configuration is missing or invalid."""

    code = "config_error"


class AuthError(SagaError):
    """Raised when a request is unauthenticated or unauthorized."""

    code = "auth_error"


class NotFoundError(SagaError):
    """Raised when a requested resource does not exist."""

    code = "not_found"


class ValidationError(SagaError):
    """Raised when client input fails validation."""

    code = "validation_error"


class ConflictError(SagaError):
    """Raised on a conflicting state, e.g. a rejected duplicate upload (FR-13)."""

    code = "conflict"


class ConversionError(SagaError):
    """Raised when a document cannot be converted to text/Markdown."""

    code = "conversion_error"


class AnalysisError(SagaError):
    """Raised when LLM analysis/metadata extraction fails."""

    code = "analysis_error"


class StorageError(SagaError):
    """Raised on OpenSearch/MinIO/Redis storage failures."""

    code = "storage_error"


class ProviderError(SagaError):
    """Raised when an LLM/embedding provider call fails."""

    code = "provider_error"
