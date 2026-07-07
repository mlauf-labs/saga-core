"""Tests for saga-reproject: reproject_all rebuilds the projection from Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from saga.core.models import Document, FolderRef
from saga.scripts.reproject import reproject_all
from saga.storage.opensearch import ProjectionRecord
from tests.conftest import InMemoryProjection


def _doc(
    doc_id: str,
    *,
    folder_ids: list[str] | None = None,
    summary: str | None = None,
) -> Document:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Document(
        document_id=doc_id,
        title=f"t-{doc_id}",
        summary=summary,
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="o",
        folders=[FolderRef(folder_id=fid, name=fid) for fid in folder_ids or []],
        created_at=now,
        updated_at=now,
    )


class FakeSource:
    """ReprojectSource fake with id-cursor pagination mirroring scroll_documents."""

    def __init__(
        self,
        docs: list[Document],
        embeddings: dict[str, list[float]],
        parents: dict[str, str | None] | None = None,
    ) -> None:
        self._docs = sorted(docs, key=lambda d: d.document_id)
        self._embeddings = embeddings
        self._parents = parents or {}
        self.parents_calls = 0

    async def parents_map(self) -> dict[str, str | None]:
        self.parents_calls += 1
        return dict(self._parents)

    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = None
    ) -> tuple[list[Document], str | None]:
        docs = [d for d in self._docs if after_id is None or d.document_id > after_id]
        page = docs[:page_size]
        next_cursor = page[-1].document_id if page and len(page) == page_size else None
        return page, next_cursor

    async def get_summary_embeddings(self, document_ids: list[str]) -> dict[str, list[float]]:
        return {i: self._embeddings[i] for i in document_ids if i in self._embeddings}

    async def document_ids(self) -> set[str]:
        return {d.document_id for d in self._docs}


class RejectingSink(InMemoryProjection):
    """Projection sink that rejects configured document ids (poison documents)."""

    def __init__(self, reject: set[str]) -> None:
        super().__init__()
        self._reject = reject

    async def project_documents(self, records: list[ProjectionRecord]) -> list[str]:
        failed = [r.document.document_id for r in records if r.document.document_id in self._reject]
        await super().project_documents(
            [r for r in records if r.document.document_id not in self._reject]
        )
        return failed


@pytest.mark.asyncio
async def test_reproject_all_projects_with_stored_embeddings() -> None:
    src = FakeSource(
        [_doc("a"), _doc("b", summary="s"), _doc("c")], {"a": [0.1, 0.2], "c": [0.3, 0.4]}
    )
    sink = InMemoryProjection()
    stats = await reproject_all(src, sink, page_size=10)
    assert stats.projected == 3
    assert stats.failed == []
    # each document keeps its STORED embedding (None when absent) — no re-embedding.
    assert {i: p["summary_embedding"] for i, p in sink.projected.items()} == {
        "a": [0.1, 0.2],
        "b": None,
        "c": [0.3, 0.4],
    }
    # "b" has a summary but no stored embedding — surfaced, not silently dropped.
    assert stats.missing_embedding == 1


@pytest.mark.asyncio
async def test_reproject_all_paginates_with_cursor() -> None:
    src = FakeSource([_doc(str(i)) for i in range(5)], {})
    sink = InMemoryProjection()
    stats = await reproject_all(src, sink, page_size=2)
    assert stats.projected == 5
    assert sorted(sink.projected) == ["0", "1", "2", "3", "4"]
    # parents_map is refreshed once per page (folders can move mid-run), not per document.
    assert src.parents_calls == 3


@pytest.mark.asyncio
async def test_reproject_all_computes_folder_ancestors() -> None:
    src = FakeSource(
        [_doc("a", folder_ids=["f2"])],
        {},
        parents={"f2": "f1", "f1": None},
    )
    sink = InMemoryProjection()
    await reproject_all(src, sink)
    assert sink.projected["a"]["folder_ancestor_ids"] == ["f2", "f1"]


@pytest.mark.asyncio
async def test_reproject_all_skips_rejected_documents() -> None:
    src = FakeSource([_doc("a"), _doc("b"), _doc("c")], {})
    sink = RejectingSink({"b"})
    stats = await reproject_all(src, sink, page_size=2)
    # one poison document must not abort the rebuild — it is reported instead.
    assert stats.failed == ["b"]
    assert stats.projected == 2
    assert sorted(sink.projected) == ["a", "c"]


@pytest.mark.asyncio
async def test_reproject_all_deletes_stale_projections() -> None:
    src = FakeSource([_doc("a")], {})
    sink = InMemoryProjection()
    await sink.project_document(_doc("ghost"), folder_ancestor_ids=[])
    stats = await reproject_all(src, sink)
    assert stats.stale_deleted == 1
    assert sorted(sink.projected) == ["a"]


@pytest.mark.asyncio
async def test_reproject_all_omits_mismatched_embedding_dimension() -> None:
    src = FakeSource([_doc("a"), _doc("b")], {"a": [0.1, 0.2], "b": [0.3]})
    sink = InMemoryProjection()
    stats = await reproject_all(src, sink, expected_embedding_dim=2)
    # a wrong-dimension vector cannot enter the kNN field — omitted and counted.
    assert sink.projected["a"]["summary_embedding"] == [0.1, 0.2]
    assert sink.projected["b"]["summary_embedding"] is None
    assert stats.mismatched_embedding == 1


@pytest.mark.asyncio
async def test_reproject_all_empty() -> None:
    src = FakeSource([], {})
    sink = InMemoryProjection()
    stats = await reproject_all(src, sink)
    assert stats.projected == 0
    assert sink.projected == {}
