from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Document, Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService
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


async def test_write_bundle_emits_manifest_and_events(store: PostgresStore) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    parent = await store.create_folder(
        name="Finanzen", description="Money", emoji="💰", metadata={"color": "green"}
    )
    child = await store.create_folder(name="2026", parent_id=parent.folder_id)
    await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")
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
        doc.document_id, folder_ids=[child.folder_id], primary_id=child.folder_id
    )
    await store.append_event(
        Event(
            event_id="e-audit",
            category=EventCategory.AUDIT,
            event_type=EventType.FOLDER_CREATED,
            folder_id=parent.folder_id,
            recorded_at=now,
            actor="system",
            summary="Created folder Finanzen",
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

    builder = OkfBundleBuilder(
        db=store,
        minio=_Minio(),
        timeline=TimelineService(store),
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
        assert f"{root}/saga-manifest.json" in names
        assert f"{root}/saga-events.jsonl" in names

        manifest_member = tar.extractfile(f"{root}/saga-manifest.json")
        assert manifest_member is not None
        manifest = json.loads(manifest_member.read().decode())
        assert manifest["store"] == "saga"
        folder_names = {f["name"] for f in manifest["folders"]}
        assert {"Finanzen", "2026"} <= folder_names
        finanzen = next(f for f in manifest["folders"] if f["name"] == "Finanzen")
        assert finanzen["emoji"] == "💰"
        assert finanzen["metadata"] == {"color": "green"}
        child_entry = next(f for f in manifest["folders"] if f["name"] == "2026")
        assert child_entry["parent_id"] == parent.folder_id
        assert any(dt["name"] == "invoice" and dt["emoji"] == "📄" for dt in manifest["doc_types"])

        events_member = tar.extractfile(f"{root}/saga-events.jsonl")
        assert events_member is not None
        event_ids = {
            json.loads(line)["event_id"] for line in events_member.read().decode().splitlines()
        }
        assert event_ids == {"e-audit", "e-content"}
