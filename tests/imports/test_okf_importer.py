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
from saga.events import EventQuery, TimelineService
from saga.export.okf import render_concept
from saga.imports.okf import OkfBundleImporter
from saga.pipeline.queue import INDEX_JOB, INGEST_JOB
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
    importer = OkfBundleImporter(db=store, minio=minio, queue=FakeQueue(), config=AppConfig())
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")
    # A with-originals export places the binary next to the concept (same basename).
    sidecar = tmp_path / f"{backup_basename('doc-1', 'Rechnung ACME')}.pdf"
    sidecar.write_bytes(b"%PDF-1.7 real bytes")

    await importer._restore_document(concept, doctype_ids={}, folder_map={})

    # The exact original binary (not the markdown body) is stored under the document id.
    assert minio.objects["doc-1"] == b"%PDF-1.7 real bytes"


async def test_restore_events_remaps_folder_id(store: PostgresStore) -> None:
    importer = _importer(store)
    now = datetime(2026, 5, 1, tzinfo=UTC)
    created = await store.create_folder(name="Finanzen")
    folder_map = {"f-src": created.folder_id}
    events = [
        Event(
            event_id="e-audit",
            category=EventCategory.AUDIT,
            event_type=EventType.FOLDER_CREATED,
            folder_id="f-src",
            recorded_at=now,
            actor="system",
            summary="created",
        ),
        Event(
            event_id="e-content",
            category=EventCategory.CONTENT,
            event_type=EventType.DATED_FACT,
            document_id="d1",
            occurred_at=now,
            recorded_at=now,
            actor="llm",
            summary="dated",
        ),
    ]

    restored, skipped = await importer._restore_events(events, folder_map)

    assert (restored, skipped) == (2, 0)
    stored = {e.event_id: e for e in await TimelineService(store).query(EventQuery(limit=100))}
    assert stored["e-audit"].folder_id == created.folder_id
    assert stored["e-content"].folder_id is None
    # Idempotent re-restore inserts nothing.
    assert await importer._restore_events(events, folder_map) == (0, 2)


async def test_reenrich_foreign_bundle(tmp_path: Path, store: PostgresStore) -> None:
    queue = FakeQueue()
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=queue, config=AppConfig()
    )
    root = tmp_path / "okf-foreign"
    (root / "Taxes").mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "Taxes" / "index.md").write_text("# Taxes\n", encoding="utf-8")
    (root / "Taxes" / "note.md").write_text(
        "---\ntype: invoice\ntitle: A Foreign Bill\ndescription: Imported note.\n---\n\n# Hello\n",
        encoding="utf-8",
    )

    dir_to_id = await importer._restore_foreign_folders(root)
    folder_names = set()
    for fid in dir_to_id.values():
        folder = await store.get_folder(fid)
        assert folder is not None
        folder_names.add(folder.name)
    assert folder_names == {"Taxes"}

    result = await importer._reenrich_concept(root / "Taxes" / "note.md", dir_to_id)
    assert result == "imported"

    docs, _ = await store.list_documents(page=1, page_size=10)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "A Foreign Bill"
    assert doc.doc_type == "invoice"  # seeded -> ensure_doc_type
    assert doc.summary == "Imported note."
    # The doc is placed in the rebuilt Taxes folder, and the FULL pipeline is enqueued.
    taxes_id = dir_to_id[root / "Taxes"]
    assert [r.folder_id for r in await store.get_document_folders(doc.document_id)] == [taxes_id]
    assert (INGEST_JOB, (doc.document_id,)) in queue.jobs


async def test_restore_foreign_folders_idempotent(tmp_path: Path, store: PostgresStore) -> None:
    importer = _importer(store)
    root = tmp_path / "okf-foreign"
    (root / "Taxes").mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "Taxes" / "index.md").write_text("# Taxes\n", encoding="utf-8")

    first = await importer._restore_foreign_folders(root)
    second = await importer._restore_foreign_folders(root)  # must not raise / duplicate

    assert first == second
    assert len(await store.list_folders()) == 1


async def test_run_faithful_collects_summary_and_is_robust(
    tmp_path: Path, store: PostgresStore
) -> None:
    importer = _importer(store)
    root = tmp_path / "okf-saga-1"
    root.mkdir()
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "saga-manifest.json").write_text(
        json.dumps(
            {
                "version": "1",
                "store": "saga",
                "folders": [
                    {
                        "id": "f-src",
                        "name": "Finanzen",
                        "parent_id": None,
                        "description": "Money",
                        "emoji": "💰",
                        "metadata": {},
                    }
                ],
                "doc_types": [
                    {"id": "dt", "name": "invoice", "description": "A bill.", "emoji": "📄"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "Rechnung-ACME__doc-1.md").write_text(_concept_text("f-src"), encoding="utf-8")
    # A malformed concept must not abort the whole import.
    (root / "broken__x.md").write_text("---\ntitle: no saga id\n---\n\nbody\n", encoding="utf-8")
    now = datetime(2026, 5, 1, tzinfo=UTC)
    ev = Event(
        event_id="e1",
        category=EventCategory.AUDIT,
        event_type=EventType.FOLDER_CREATED,
        folder_id="f-src",
        recorded_at=now,
        actor="system",
        summary="created",
    )
    (root / "saga-events.jsonl").write_text(
        json.dumps(ev.model_dump(mode="json")) + "\n", encoding="utf-8"
    )

    summary = await importer.run(tmp_path)

    assert summary.documents_imported == 1
    assert summary.documents_failed == 1  # broken__x.md (no saga_id)
    assert summary.folders_created == 1
    assert summary.doc_types_created == 1
    assert summary.events_restored == 1
    assert any("broken__x.md" in e for e in summary.errors)
    assert await store.get_document("doc-1") is not None


async def test_run_foreign_routes_to_reenrich(tmp_path: Path, store: PostgresStore) -> None:
    queue = FakeQueue()
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=queue, config=AppConfig()
    )
    root = tmp_path / "okf-foreign"
    (root / "Taxes").mkdir(parents=True)
    (root / "index.md").write_text("# Index\n", encoding="utf-8")
    (root / "Taxes" / "index.md").write_text("# Taxes\n", encoding="utf-8")
    (root / "Taxes" / "note.md").write_text(
        "---\ntype: invoice\ntitle: Bill\n---\n\n# Hello\n", encoding="utf-8"
    )

    summary = await importer.run(tmp_path)

    assert summary.documents_imported == 1
    assert summary.folders_created == 1
    assert any(job == INGEST_JOB for job, _ in queue.jobs)
