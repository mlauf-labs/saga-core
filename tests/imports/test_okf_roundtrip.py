"""Headline guarantee (spec section 8/section 10): export -> import reproduces the same state."""

from __future__ import annotations

import io
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.config import AppConfig
from saga.core.models import (
    Document,
    DocumentStatus,
    Event,
    EventCategory,
    EventType,
    ExtractedValue,
)
from saga.events import EventQuery, TimelineService
from saga.export.okf import OkfBundleBuilder
from saga.imports.okf import OkfBundleImporter
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeQueue, InMemoryBinaryStore


async def _fresh_store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


@pytest_asyncio.fixture
async def source() -> PostgresStore:
    return await _fresh_store()


async def _seed(store: PostgresStore) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    finanzen = await store.create_folder(name="Finanzen", description="Money", emoji="💰")
    y2026 = await store.create_folder(name="2026", parent_id=finanzen.folder_id)
    invoice_type = await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")
    await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="abc123",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            doc_type_id=invoice_type.doc_type_id,
            summary="One invoice.",
            content_markdown="# Body\n\nLine two.",
            extracted_values=[ExtractedValue(key="total", type="money", value="9.99")],
            created_at=now,
            updated_at=now,
        )
    )
    await store.add_document_note("d1", "Check me")
    await store.set_document_folders("d1", folder_ids=[y2026.folder_id], primary_id=y2026.folder_id)
    await store.append_event(
        Event(
            event_id="e-audit",
            category=EventCategory.AUDIT,
            event_type=EventType.FOLDER_CREATED,
            folder_id=finanzen.folder_id,
            recorded_at=now,
            actor="system",
            summary="Created Finanzen",
        )
    )
    await store.append_event(
        Event(
            event_id="e-content",
            category=EventCategory.CONTENT,
            event_type=EventType.DATED_FACT,
            document_id="d1",
            occurred_at=now,
            recorded_at=now,
            actor="llm",
            summary="Invoice dated 2026-05-01",
        )
    )


async def test_export_then_import_reproduces_state(source: PostgresStore) -> None:
    await _seed(source)
    src_minio = InMemoryBinaryStore()
    builder = OkfBundleBuilder(
        db=source,
        minio=src_minio,
        timeline=TimelineService(source),
        store_name="saga",
        public_base_url=None,
        with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)

    target = await _fresh_store()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            extract_dir = Path(tmp) / "bundle"
            extract_dir.mkdir()
            buf.seek(0)
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                tar.extractall(extract_dir, filter="data")
            importer = OkfBundleImporter(
                db=target, minio=InMemoryBinaryStore(), queue=FakeQueue(), config=AppConfig()
            )
            summary = await importer.run(extract_dir)

        assert summary.documents_imported == 1
        assert summary.events_restored == 2

        restored = await target.get_document("d1")
        assert restored is not None
        assert restored.title == "Rechnung ACME"
        assert restored.content_markdown == "# Body\n\nLine two."
        assert restored.summary == "One invoice."
        assert restored.doc_type == "invoice"
        assert restored.status == DocumentStatus.READY
        assert restored.mime_type == "application/pdf"
        assert restored.content_hash == "abc123"
        assert [(v.key, v.value) for v in restored.extracted_values] == [("total", "9.99")]
        assert [n.content for n in restored.notes] == ["Check me"]  # get_document loads notes

        folders = {f.name: f for f in await target.list_folders()}
        assert set(folders) == {"Finanzen", "2026"}
        assert folders["Finanzen"].emoji == "💰"
        assert folders["2026"].parent_id == folders["Finanzen"].folder_id

        refs = await target.get_document_folders("d1")
        assert [r.folder_id for r in refs] == [folders["2026"].folder_id]
        assert refs[0].is_primary is True

        doc_types = {dt.name: dt for dt in await target.list_doc_types()}
        assert doc_types["invoice"].description == "A bill." and doc_types["invoice"].emoji == "📄"

        events = {e.event_id: e for e in await TimelineService(target).query(EventQuery(limit=100))}
        assert set(events) == {"e-audit", "e-content"}
        assert events["e-audit"].folder_id == folders["Finanzen"].folder_id
        assert events["e-content"].folder_id is None
        assert events["e-content"].event_type == EventType.DATED_FACT
    finally:
        await target.close()
