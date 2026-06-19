"""Rebuild the OpenSearch document projection from Postgres (the system of record).

``saga-reproject`` first ensures the indices exist with the current mapping (``bootstrap``
repairs a stale/drifted mapping by recreating the index — see ``OpenSearchStore.bootstrap``),
then re-projects every document. It reuses the **stored** summary embedding for each document,
so it does not re-run the LLM or the embedding model.

Use it to:
* repopulate the search index after a mapping-drift recreate (pairs with the bootstrap repair),
* recover the search projection if an index is lost (Postgres is unaffected),
* refresh the projection after a bulk data change.

Usage::

    saga-reproject               # bootstrap (repair mapping) + reproject all documents
    saga-reproject --page-size 100
"""

from __future__ import annotations

import argparse
import asyncio
from typing import TYPE_CHECKING, Protocol

from saga.core.config import load_config
from saga.core.logging import configure_logging, get_logger
from saga.storage import OpenSearchStore, PostgresStore
from saga.storage.postgres import ancestor_ids

if TYPE_CHECKING:
    from collections.abc import Callable

    from saga.core.models import Document

_log = get_logger("saga.scripts.reproject")

#: Documents fetched per page from Postgres while reprojecting.
DEFAULT_PAGE_SIZE = 200


class ReprojectSource(Protocol):
    """The Postgres reads reproject_all needs (satisfied by PostgresStore)."""

    async def parents_map(self) -> dict[str, str | None]: ...
    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]: ...
    async def get_summary_embedding(self, document_id: str) -> list[float] | None: ...


class ReprojectSink(Protocol):
    """The OpenSearch write reproject_all needs (satisfied by OpenSearchStore)."""

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None: ...


async def reproject_all(
    db: ReprojectSource,
    opensearch: ReprojectSink,
    *,
    page_size: int = DEFAULT_PAGE_SIZE,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Re-project every document in Postgres to OpenSearch; return the number projected.

    The folder ``parents_map`` is fetched once (not per document). Each document keeps its
    stored ``summary_embedding`` — no re-embedding. ``on_progress(done, total)`` is called
    after each document when provided.
    """
    parents = await db.parents_map()
    done = 0
    page = 1
    while True:
        documents, total = await db.list_documents(page=page, page_size=page_size)
        for document in documents:
            embedding = await db.get_summary_embedding(document.document_id)
            await opensearch.project_document(
                document,
                folder_ancestor_ids=ancestor_ids(document.folder_ids, parents),
                summary_embedding=embedding,
            )
            done += 1
            if on_progress is not None:
                on_progress(done, total)
        if not documents or page * page_size >= total:
            return done
        page += 1


async def _run(*, page_size: int) -> None:
    config = load_config()
    db = PostgresStore(config.postgres)
    opensearch = OpenSearchStore(config.opensearch)
    try:
        # bootstrap recreates an index whose mapping has drifted (current mapping), so the
        # reproject below writes into a correctly-mapped, possibly-just-recreated index.
        await opensearch.bootstrap()

        def _progress(done: int, total: int) -> None:
            if done == 1 or done % 100 == 0 or done == total:
                _log.info("reproject_progress", done=done, total=total)

        count = await reproject_all(db, opensearch, page_size=page_size, on_progress=_progress)
        _log.info("reproject_complete", documents=count)
    finally:
        await db.close()
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
    configure_logging()
    asyncio.run(_run(page_size=args.page_size))


if __name__ == "__main__":
    main()
