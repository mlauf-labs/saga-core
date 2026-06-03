"""MinIO object storage for original binaries (FR-9). Implemented in Phase 1."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docstore.core.config import MinioConfig


class MinioStore:
    """Stores and retrieves original document binaries."""

    def __init__(self, config: MinioConfig) -> None:
        self._config = config

    async def bootstrap(self) -> None:
        """Ensure the configured bucket exists."""
        raise NotImplementedError("MinIO bootstrap is implemented in Phase 1.")
