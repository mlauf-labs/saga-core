from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Document, Event
from saga.events import EventQuery
from saga.export.okf import OkfBundleBuilder
from saga.storage.postgres import PostgresStore


class _Timeline:
    async def query(self, q: EventQuery) -> list[Event]:
        return []


class _Minio:
    async def get_object(self, object_name: str) -> bytes:
        return b"PDFBYTES"


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def _seed(store: PostgresStore) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folder = await store.create_folder(name="Finanzen", description="Money")
    doc = await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="h",
            minio_object="saga-originals/d1",
            doc_type="invoice",
            summary="One invoice.",
            content_markdown="# Body",
            created_at=now,
            updated_at=now,
        )
    )
    await store.set_document_folders(
        doc.document_id, folder_ids=[folder.folder_id], primary_id=folder.folder_id
    )


async def test_write_bundle_lays_out_index_and_concept_files(store: PostgresStore) -> None:
    await _seed(store)
    builder = OkfBundleBuilder(
        db=store,
        minio=_Minio(),
        timeline=_Timeline(),
        store_name="saga",
        public_base_url=None,
        with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
        root = names[0].split("/")[0]
        assert f"{root}/index.md" in names
        assert any(n.startswith(f"{root}/Finanzen/") and n.endswith(".md") for n in names)
        concept = next(n for n in names if "Rechnung" in n and n.endswith(".md"))
        member = tar.extractfile(concept)
        assert member is not None
        assert member.read().decode().startswith("---\n")


async def test_write_bundle_with_originals_includes_binary(store: PostgresStore) -> None:
    await _seed(store)
    builder = OkfBundleBuilder(
        db=store,
        minio=_Minio(),
        timeline=_Timeline(),
        store_name="saga",
        public_base_url=None,
        with_originals=True,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        assert any(n.endswith(".pdf") for n in tar.getnames())
