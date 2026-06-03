"""OpenSearch storage: index bootstrap, document/chunk CRUD, hybrid search (Phase 1/6).

Manages the ``documents`` (keyword) and ``document_chunks`` (kNN) indices and the
hybrid search pipeline (FR-25). Deleting a document cascades to its chunks (FR-26).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docstore.core.config import OpenSearchConfig


class OpenSearchStore:
    """Thin, typed wrapper around the OpenSearch client."""

    def __init__(self, config: OpenSearchConfig) -> None:
        self._config = config

    async def bootstrap(self) -> None:
        """Create indices (with kNN mapping) and the hybrid search pipeline."""
        raise NotImplementedError("OpenSearch bootstrap is implemented in Phase 1.")
