from __future__ import annotations

import json
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
    FolderRef,
    Note,
)
from saga.export.okf import render_concept
from saga.imports.okf import OkfBundleImporter
from saga.pipeline.queue import INDEX_JOB
from saga.scripts.layout import backup_basename
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeQueue, InMemoryBinaryStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


def _importer(store: PostgresStore) -> OkfBundleImporter:
    return OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=FakeQueue(), config=AppConfig()
    )


async def test_restore_doc_types_idempotent(store: PostgresStore) -> None:
    importer = _importer(store)
    doc_types = [
        {"id": "src1", "name": "invoice", "description": "A bill.", "emoji": "📄"},
        {"id": "src2", "name": "contract", "description": None, "emoji": None},
    ]
    name_to_id, created, reused = await importer._restore_doc_types(doc_types)
    assert created == 2 and reused == 0
    assert set(name_to_id) == {"invoice", "contract"}
    _, created2, reused2 = await importer._restore_doc_types(doc_types)
    assert created2 == 0 and reused2 == 2


async def test_restore_folders_topological_with_id_map(store: PostgresStore) -> None:
    importer = _importer(store)
    folders = [
        {
            "id": "c",
            "name": "2026",
            "parent_id": "p",
            "description": None,
            "emoji": None,
            "metadata": {},
        },
        {
            "id": "p",
            "name": "Finanzen",
            "parent_id": None,
            "description": "Money",
            "emoji": "💰",
            "metadata": {"color": "green"},
        },
    ]
    id_map, created, reused = await importer._restore_folders(folders)
    assert created == 2 and reused == 0
    parent = await store.get_folder(id_map["p"])
    child = await store.get_folder(id_map["c"])
    assert parent is not None and child is not None
    assert parent.name == "Finanzen" and parent.emoji == "💰"
    assert child.parent_id == id_map["p"]
    id_map2, created2, reused2 = await importer._restore_folders(folders)
    assert created2 == 0 and reused2 == 2
    assert id_map2["p"] == id_map["p"]


async def test_load_manifest_and_events(tmp_path: Path, store: PostgresStore) -> None:
    importer = _importer(store)
    root = tmp_path / "okf-saga-x"
    root.mkdir()
    (root / "saga-manifest.json").write_text(
        json.dumps({"version": "1", "store": "saga", "folders": [], "doc_types": []}),
        encoding="utf-8",
    )
    now = datetime(2026, 5, 1, tzinfo=UTC)
    ev = Event(
        event_id="e1",
        category=EventCategory.AUDIT,
        event_type=EventType.FOLDER_CREATED,
        recorded_at=now,
        actor="system",
        summary="x",
    )
    (root / "saga-events.jsonl").write_text(
        json.dumps(ev.model_dump(mode="json")) + "\n", encoding="utf-8"
    )

    assert importer._load_manifest(tmp_path)["store"] == "saga"
    loaded = importer._load_events(tmp_path)
    assert [e.event_id for e in loaded] == ["e1"]
    assert importer._load_manifest(tmp_path / "nonexistent-empty") is None


def _concept_text(folder_src_id: str) -> str:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    doc = Document(
        document_id="doc-1",
        title="Rechnung ACME",
        filename="rechnung.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        content_hash="abc123",
        minio_object="saga-originals/doc-1",
        status=DocumentStatus.READY,
        doc_type="invoice",
        summary="One invoice.",
        content_markdown="# Body\n\nLine two.",
        extracted_values=[ExtractedValue(key="total", type="money", value="9.99")],
        folders=[FolderRef(folder_id=folder_src_id, name="Finanzen", is_primary=True)],
        notes=[Note(note_id="n1", content="Check me", created_at=now, updated_at=now)],
        created_at=now,
        updated_at=now,
    )
    return render_concept(doc, store_name="saga", public_base_url=None)


async def test_restore_document_faithful(tmp_path: Path, store: PostgresStore) -> None:
    queue = FakeQueue()
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=queue, config=AppConfig()
    )
    doctype_ids, _, _ = await importer._restore_doc_types(
        [{"id": "dt-src", "name": "invoice", "description": "A bill.", "emoji": "📄"}]
    )
    folder_map, _, _ = await importer._restore_folders(
        [
            {
                "id": "f-src",
                "name": "Finanzen",
                "parent_id": None,
                "description": "Money",
                "emoji": "💰",
                "metadata": {},
            }
        ]
    )
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")

    result = await importer._restore_document(
        concept, doctype_ids=doctype_ids, folder_map=folder_map
    )

    assert result == "imported"
    restored = await store.get_document("doc-1")
    assert restored is not None
    assert restored.title == "Rechnung ACME"
    assert restored.content_markdown == "# Body\n\nLine two."
    assert restored.summary == "One invoice."
    assert restored.doc_type == "invoice"
    assert restored.status == DocumentStatus.READY
    assert restored.mime_type == "application/pdf"
    assert restored.content_hash == "abc123"
    assert [v.key for v in restored.extracted_values] == ["total"]
    assert [n.content for n in restored.notes] == ["Check me"]  # get_document loads notes
    refs = await store.get_document_folders("doc-1")
    assert [r.folder_id for r in refs] == [folder_map["f-src"]]
    assert refs[0].is_primary is True
    assert (INDEX_JOB, ("doc-1",)) in queue.jobs


async def test_restore_document_skips_existing_saga_id(
    tmp_path: Path, store: PostgresStore
) -> None:
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=FakeQueue(), config=AppConfig()
    )
    importer._config.dedup.on_duplicate = "reject"
    folder_map, _, _ = await importer._restore_folders(
        [
            {
                "id": "f-src",
                "name": "Finanzen",
                "parent_id": None,
                "description": None,
                "emoji": None,
                "metadata": {},
            }
        ]
    )
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")
    await importer._restore_document(concept, doctype_ids={}, folder_map=folder_map)

    result = await importer._restore_document(concept, doctype_ids={}, folder_map=folder_map)
    assert result == "skipped"


async def test_restore_document_replace_dedup(tmp_path: Path, store: PostgresStore) -> None:
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=FakeQueue(), config=AppConfig()
    )
    importer._config.dedup.on_duplicate = "replace"
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")
    await importer._restore_document(concept, doctype_ids={}, folder_map={})

    # Re-import the same saga_id: "replace" deletes then recreates (not skipped).
    result = await importer._restore_document(concept, doctype_ids={}, folder_map={})

    assert result == "imported"
    docs, total = await store.list_documents(page=1, page_size=10)
    assert total == 1  # no duplicate row
    assert docs[0].document_id == "doc-1"


async def test_restore_document_restores_sidecar_binary(
    tmp_path: Path, store: PostgresStore
) -> None:
    minio = InMemoryBinaryStore()
    importer = OkfBundleImporter(
        db=store, minio=minio, queue=FakeQueue(), config=AppConfig()
    )
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")
    # A with-originals export places the binary next to the concept (same basename).
    sidecar = tmp_path / f"{backup_basename('doc-1', 'Rechnung ACME')}.pdf"
    sidecar.write_bytes(b"%PDF-1.7 real bytes")

    await importer._restore_document(concept, doctype_ids={}, folder_map={})

    # The exact original binary (not the markdown body) is stored under the document id.
    assert minio.objects["doc-1"] == b"%PDF-1.7 real bytes"
