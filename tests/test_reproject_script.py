"""Tests for saga-reproject: reproject_all rebuilds the projection from Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from saga.core.models import Document
from saga.scripts.reproject import reproject_all


def _doc(doc_id: str) -> Document:
    now = datetime(2026, 6, 1, tzinfo=UTC)
    return Document(
        document_id=doc_id,
        title=f"t-{doc_id}",
        mime_type="application/pdf",
        size_bytes=1,
        content_hash="h",
        minio_object="o",
        created_at=now,
        updated_at=now,
    )


class FakeSource:
    def __init__(self, docs: list[Document], embeddings: dict[str, list[float]]) -> None:
        self._docs = docs
        self._embeddings = embeddings
        self.parents_calls = 0

    async def parents_map(self) -> dict[str, str | None]:
        self.parents_calls += 1
        return {}

    async def list_documents(self, *, page: int, page_size: int) -> tuple[list[Document], int]:
        start = (page - 1) * page_size
        return self._docs[start : start + page_size], len(self._docs)

    async def get_summary_embedding(self, document_id: str) -> list[float] | None:
        return self._embeddings.get(document_id)


class FakeSink:
    def __init__(self) -> None:
        self.projected: list[tuple[str, list[float] | None]] = []

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected.append((document.document_id, summary_embedding))


@pytest.mark.asyncio
async def test_reproject_all_projects_with_stored_embeddings() -> None:
    src = FakeSource([_doc("a"), _doc("b"), _doc("c")], {"a": [0.1, 0.2], "c": [0.3]})
    sink = FakeSink()
    count = await reproject_all(src, sink, page_size=10)
    assert count == 3
    # each document keeps its STORED embedding (None when absent) — no re-embedding.
    assert sink.projected == [("a", [0.1, 0.2]), ("b", None), ("c", [0.3])]
    assert src.parents_calls == 1  # parents_map fetched once, not per document


@pytest.mark.asyncio
async def test_reproject_all_paginates() -> None:
    src = FakeSource([_doc(str(i)) for i in range(5)], {})
    sink = FakeSink()
    count = await reproject_all(src, sink, page_size=2)
    assert count == 5
    assert [doc_id for doc_id, _ in sink.projected] == ["0", "1", "2", "3", "4"]
    assert src.parents_calls == 1


@pytest.mark.asyncio
async def test_reproject_all_reports_progress() -> None:
    src = FakeSource([_doc("x"), _doc("y")], {})
    sink = FakeSink()
    seen: list[tuple[int, int]] = []

    def _progress(done: int, total: int) -> None:
        seen.append((done, total))

    await reproject_all(src, sink, page_size=10, on_progress=_progress)
    assert seen == [(1, 2), (2, 2)]


@pytest.mark.asyncio
async def test_reproject_all_empty() -> None:
    src = FakeSource([], {})
    sink = FakeSink()
    assert await reproject_all(src, sink) == 0
    assert sink.projected == []
