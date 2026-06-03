"""Exception hierarchy with actionable error messages (NFR-15).

Every exception carries a human-readable message describing *what* failed and,
where possible, *how* to fix it. API/MCP layers map these to appropriate
responses.
"""

from __future__ import annotations


class DocStoreError(Exception):
    """Base class for all DocStore errors."""

    #: Stable, machine-readable error code surfaced to API clients.
    code: str = "docstore_error"


class ConfigError(DocStoreError):
    """Raised when configuration is missing or invalid."""

    code = "config_error"


class AuthError(DocStoreError):
    """Raised when a request is unauthenticated or unauthorized."""

    code = "auth_error"


class NotFoundError(DocStoreError):
    """Raised when a requested resource does not exist."""

    code = "not_found"


class ValidationError(DocStoreError):
    """Raised when client input fails validation."""

    code = "validation_error"


class ConversionError(DocStoreError):
    """Raised when a document cannot be converted to text/Markdown."""

    code = "conversion_error"


class AnalysisError(DocStoreError):
    """Raised when LLM analysis/metadata extraction fails."""

    code = "analysis_error"


class StorageError(DocStoreError):
    """Raised on OpenSearch/MinIO/Redis storage failures."""

    code = "storage_error"


class ProviderError(DocStoreError):
    """Raised when an LLM/embedding provider call fails."""

    code = "provider_error"
