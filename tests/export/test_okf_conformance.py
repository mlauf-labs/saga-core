"""Local OKF v0.1 conformance check for the exported bundle (roadmap Track I).

This is the *in-repo* half of interoperability: it does not need a live OKF consumer.
It builds a representative bundle and asserts every file honours the OKF v0.1 contract, so
a regression that breaks frontmatter, leaks frontmatter into a reserved file, or adds an
unexpected non-markdown file (which an OKF consumer would have to know to skip) fails here.

OKF v0.1 contract enforced:
- Concept files (any ``*.md`` other than the reserved names) start with a ``---`` YAML
  frontmatter block that parses to a mapping with a non-empty ``type`` (required) and a
  ``title``; ``tags`` when present is a list.
- Reserved per-directory files ``index.md`` / ``log.md`` carry NO frontmatter — they open
  with a markdown ``#`` heading.
- The only non-markdown files are SAGA's machine-readable extras (``saga-manifest.json`` /
  ``saga-events.jsonl``), which OKF consumers ignore. Originals are excluded here.
"""

from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime

import pytest_asyncio
import yaml
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Document, Event, EventCategory, EventType
from saga.events import TimelineService
from saga.export.okf import OkfBundleBuilder
from saga.storage.postgres import PostgresStore

_RESERVED_MARKDOWN = {"index.md", "log.md"}
_ALLOWED_NON_MARKDOWN = {"saga-manifest.json", "saga-events.jsonl"}


class _Minio:
    async def get_object(self, object_name: str) -> bytes:
        return b"PDFBYTES"


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def _seed_representative_bundle(store: PostgresStore) -> None:
    """Seed nested folders, a filed doc (with a note), an unfiled doc, and both event kinds."""
    now = datetime(2026, 5, 1, tzinfo=UTC)
    parent = await store.create_folder(name="Finanzen", description="Money", emoji="💰")
    child = await store.create_folder(name="2026", parent_id=parent.folder_id)
    await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")

    filed = await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="h1",
            minio_object="saga-originals/d1",
            doc_type="invoice",
            summary="One invoice.",
            content_markdown="# Body",
            created_at=now,
            updated_at=now,
        )
    )
    await store.set_document_folders(
        filed.document_id, folder_ids=[child.folder_id], primary_id=child.folder_id
    )
    await store.add_document_note(filed.document_id, "Check the total.")

    # An unfiled document (no folder) exercises the ``_unfiled/`` index + concept path.
    await store.create_document(
        Document(
            document_id="d2",
            title="Loose Note",
            filename="note.txt",
            mime_type="text/plain",
            size_bytes=4,
            content_hash="h2",
            minio_object="saga-originals/d2",
            doc_type=None,  # exercises the ``type: document`` fallback
            summary=None,
            content_markdown="Some text.",
            created_at=now,
            updated_at=now,
        )
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
            folder_id=child.folder_id,
            occurred_at=now,
            recorded_at=now,
            actor="llm",
            summary="Invoice dated 2026-05-01",
        )
    )


async def _build_bundle(store: PostgresStore) -> dict[str, bytes]:
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
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            extracted = tar.extractfile(member)
            assert extracted is not None
            files[member.name] = extracted.read()
    return files


async def test_exported_bundle_conforms_to_okf_v0_1(store: PostgresStore) -> None:
    await _seed_representative_bundle(store)
    files = await _build_bundle(store)

    root = next(iter(files)).split("/")[0]
    concept_count = 0
    reserved_count = 0

    for name, raw in files.items():
        basename = name.rsplit("/", 1)[-1]

        if not name.endswith(".md"):
            # An OKF consumer only needs to know to skip these machine-readable extras.
            assert basename in _ALLOWED_NON_MARKDOWN, f"unexpected non-markdown file: {name}"
            continue

        text = raw.decode("utf-8")

        if basename in _RESERVED_MARKDOWN:
            reserved_count += 1
            # Reserved files carry no frontmatter — they are plain markdown.
            assert not text.startswith("---\n"), f"{name} must not have frontmatter"
            assert text.lstrip().startswith("#"), f"{name} must open with a markdown heading"
            continue

        # Everything else is a concept file: it MUST carry conformant frontmatter.
        concept_count += 1
        assert text.startswith("---\n"), f"{name} missing opening frontmatter fence"
        front, sep, _ = text[len("---\n") :].partition("\n---\n")
        assert sep, f"{name} missing closing frontmatter fence"
        fm = yaml.safe_load(front)
        assert isinstance(fm, dict), f"{name} frontmatter is not a mapping"
        assert isinstance(fm.get("type"), str) and fm["type"].strip(), (
            f"{name} must have a non-empty 'type'"
        )
        assert "title" in fm, f"{name} must have a 'title'"
        if "tags" in fm:
            assert isinstance(fm["tags"], list), f"{name} 'tags' must be a list"

    # The bundle is non-trivial: the root index, both concepts, and at least one log were seen.
    assert f"{root}/index.md" in files
    assert concept_count == 2  # the filed invoice + the unfiled note
    assert reserved_count >= 2  # root index.md + at least the unfiled/folder index.md
    assert any(name.endswith("/log.md") for name in files), "expected at least one log.md"


async def test_unfiled_concept_uses_the_document_type_fallback(store: PostgresStore) -> None:
    await _seed_representative_bundle(store)
    files = await _build_bundle(store)

    concept = next(raw for name, raw in files.items() if name.endswith("__d2.md"))
    fm = yaml.safe_load(concept.decode("utf-8").split("---\n", 2)[1])
    # A document with no doc-type still satisfies OKF's required 'type' via the fallback.
    assert fm["type"] == "document"
