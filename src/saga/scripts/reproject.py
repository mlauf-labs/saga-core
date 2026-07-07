"""Rebuild the OpenSearch document projection from Postgres (the system of record).

``saga-reproject`` verifies Postgres is reachable, ensures the indices exist with the
current mapping (``bootstrap`` repairs a stale/drifted mapping by recreating the index —
see ``OpenSearchStore.bootstrap``), re-projects every document, and removes projections
whose document no longer exists in Postgres. It reuses the **stored** summary embedding
for each document, so it does not re-run the LLM or the embedding model.

Scope: only the **document** index is rebuilt. Chunk embeddings exist solely in
OpenSearch and cannot be restored from stored data — if ``bootstrap`` had to recreate
the chunk index, the tool warns loudly and semantic chunk search stays empty until the
documents are re-analysed (``POST /documents/{id}/reanalyze``).

Use it to:
* repopulate the document index after a mapping-drift recreate (pairs with the bootstrap repair),
* recover the document projection if that index is lost (Postgres is unaffected),
* refresh the projection after a bulk data change (stale projections are removed).

Documents whose projection is rejected are skipped, logged, and reported; the exit code
is non-zero if any failed. For a fully consistent rebuild, run while the stack is
quiescent: a projection written concurrently by the live API/worker can be overwritten
with this run's slightly older snapshot.

Usage::

    saga-reproject               # bootstrap (repair mapping) + reproject all documents
    saga-reproject --page-size 100
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saga.core.config import load_config
from saga.core.logging import configure_logging, get_logger
from saga.storage import OpenSearchStore, PostgresStore
from saga.storage.opensearch import ProjectionRecord
from saga.storage.postgres import ancestor_ids

if TYPE_CHECKING:
    from saga.core.models import Document

_log = get_logger("saga.scripts.reproject")

#: Documents fetched per page from Postgres while reprojecting.
DEFAULT_PAGE_SIZE = 200


class ReprojectSource(Protocol):
    """The Postgres reads reproject_all needs (satisfied by PostgresStore)."""

    async def parents_map(self) -> dict[str, str | None]: ...
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = None
    ) -> tuple[list[Document], str | None]: ...
    async def get_summary_embeddings(self, document_ids: list[str]) -> dict[str, list[float]]: ...
    async def document_ids(self) -> set[str]: ...


class ReprojectSink(Protocol):
    """The OpenSearch writes reproject_all needs (satisfied by OpenSearchStore)."""

    async def project_documents(self, records: list[ProjectionRecord]) -> list[str]: ...
    async def document_ids(self) -> set[str]: ...
    async def delete_document(self, document_id: str) -> None: ...
    async def refresh_documents(self) -> None: ...


@dataclass(slots=True)
class ReprojectStats:
    """Outcome of a full reprojection run."""

    projected: int = 0
    failed: list[str] = field(default_factory=list)
    missing_embedding: int = 0
    mismatched_embedding: int = 0
    stale_deleted: int = 0


async def reproject_all(
    db: ReprojectSource,
    opensearch: ReprojectSink,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    expected_embedding_dim: int | None = None,
) -> ReprojectStats:
    """Re-project every document in Postgres to OpenSearch; drop stale projections.

    Pages with the id-cursor ``scroll_documents`` (stable when documents are inserted
    or deleted mid-run, unlike OFFSET pagination), refreshes the folder ``parents_map``
    per page (folders can move during a long run), batch-fetches the stored summary
    embeddings per page, and bulk-projects each page. A rejected document lands in
    ``stats.failed`` and is skipped — one poison document must not abort a rebuild.
    Stored embeddings whose length differs from ``expected_embedding_dim`` are omitted
    and counted (they cannot enter the kNN field). Finally, projections whose document
    no longer exists in Postgres are deleted, so the rebuilt index matches the SoR.
    """
    stats = ReprojectStats()
    cursor: str | None = None
    while True:
        documents, cursor = await db.scroll_documents(page_size=page_size, after_id=cursor)
        if not documents:
            break
        parents = await db.parents_map()
        ids = [document.document_id for document in documents]
        embeddings = await db.get_summary_embeddings(ids)
        records: list[ProjectionRecord] = []
        for document in documents:
            embedding = embeddings.get(document.document_id)
            if embedding is None:
                if document.summary:
                    stats.missing_embedding += 1
            elif expected_embedding_dim is not None and len(embedding) != expected_embedding_dim:
                stats.mismatched_embedding += 1
                embedding = None
            records.append(
                ProjectionRecord(
                    document=document,
                    folder_ancestor_ids=ancestor_ids(document.folder_ids, parents),
                    summary_embedding=embedding,
                )
            )
        failed = await opensearch.project_documents(records)
        stats.failed.extend(failed)
        stats.projected += len(documents) - len(failed)
        _log.info("reproject_progress", done=stats.projected, failed=len(stats.failed))
        if cursor is None:
            break
    await opensearch.refresh_documents()
    # Reconcile against a FRESH Postgres id snapshot so documents deleted mid-run are
    # removed even if this run re-projected them, and newly created ones are kept.
    stale = sorted(await opensearch.document_ids() - await db.document_ids())
    for document_id in stale:
        await opensearch.delete_document(document_id)
        stats.stale_deleted += 1
    return stats


async def _run(*, page_size: int) -> ReprojectStats:
    config = load_config()
    db = PostgresStore(config.postgres)
    opensearch = OpenSearchStore(config.opensearch)
    try:
        # Probe the system of record BEFORE the destructive bootstrap: a drifted index
        # must not be dropped while Postgres is unreachable and no rebuild can follow.
        await db.parents_map()
        # bootstrap recreates an index whose mapping has drifted (current mapping), so the
        # reproject below writes into a correctly-mapped, possibly-just-recreated index.
        recreated = await opensearch.bootstrap()
        if config.opensearch.chunk_index in recreated:
            _log.warning(
                "chunk_index_recreated_empty",
                index=config.opensearch.chunk_index,
                hint=(
                    "Chunk embeddings exist only in OpenSearch and cannot be rebuilt from "
                    "stored data; semantic chunk search stays empty until the documents "
                    "are re-analysed (POST /documents/{id}/reanalyze)."
                ),
            )
        stats = await reproject_all(
            db,
            opensearch,
            page_size=page_size,
            expected_embedding_dim=config.opensearch.vector_dimension,
        )
        _log.info(
            "reproject_complete",
            documents=stats.projected,
            failed=len(stats.failed),
            stale_deleted=stats.stale_deleted,
            missing_embedding=stats.missing_embedding,
            mismatched_embedding=stats.mismatched_embedding,
        )
        if stats.failed:
            _log.error(
                "reproject_failures",
                total=len(stats.failed),
                document_ids=stats.failed[:50],
                hint="These documents were skipped; inspect the warnings above and re-run.",
            )
        if stats.mismatched_embedding:
            _log.warning(
                "reproject_embedding_dimension_mismatch",
                documents=stats.mismatched_embedding,
                hint=(
                    "Stored embeddings do not match opensearch.vector_dimension; "
                    "re-analyse these documents to re-embed with the configured model."
                ),
            )
        return stats
    finally:
        try:
            await db.close()
        finally:
            await opensearch.close()


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(
        description="Rebuild the OpenSearch document projection from Postgres."
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Documents fetched per page (default {DEFAULT_PAGE_SIZE}).",
    )
    args = parser.parse_args()
    if args.page_size < 1:
        parser.error("--page-size must be a positive integer.")
    configure_logging()
    stats = asyncio.run(_run(page_size=args.page_size))
    if stats.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
