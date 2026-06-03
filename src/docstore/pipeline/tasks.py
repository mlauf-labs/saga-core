"""ARQ worker tasks for the ingestion pipeline (NFR-11).

The ``ingest_document`` job runs the durable pipeline:
convert -> analyse -> chunk -> embed -> index, updating document status at each
stage and storing an actionable error on failure (FR-12 / NFR-14/15).
"""

from __future__ import annotations

from typing import Any


async def ingest_document(ctx: dict[str, Any], document_id: str) -> None:
    """Run the full ingestion pipeline for a previously uploaded document.

    Implemented incrementally across Phases 3 (convert), 4 (analyse),
    and 5 (chunk/embed/index).
    """
    raise NotImplementedError("Ingestion pipeline is implemented in Phases 3-5.")
