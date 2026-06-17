# OKF Import / Faithful Round-Trip (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OKF bundle **import** so a SAGA-exported bundle re-imports into the exact same state (documents, metadata, content, notes, doc-types, folder tree, memberships, events), and a foreign OKF bundle is re-enriched through the normal pipeline.

**Architecture:** A FastAPI-independent `OkfBundleImporter` reads an extracted bundle directory. If `saga-manifest.json` is present → **faithful restore**: doc-types → folders (topological, building a source-folder-id→new-id map) → documents (trust the frontmatter, `document_id` preserved) → events (verbatim, `folder_id` remapped, idempotent by `event_id`) → enqueue a new non-LLM `index_document` worker job. No manifest → **re-enrich**: rebuild folders from the directory tree, seed each concept's `title`/`type`/`description` into a new document, and enqueue the full `ingest_document` pipeline. A thin `POST /import/okf` route extracts the uploaded `.tar.gz` and runs the importer; a `saga-import-okf` client mirrors the exporter. The importer uses **store** methods directly (never the event-emitting service wrappers) so restored audit events are not duplicated.

**Tech Stack:** Python 3.12, FastAPI (multipart upload), Pydantic v2 (`model_validate_json`, `model_copy`), ARQ worker, SQLAlchemy async (sqlite+aiosqlite in tests), `tarfile`, `yaml`, pytest + pytest-asyncio (`asyncio_mode=auto`), uv.

**Baseline branch:** `feature/okf-import`, created from `develop` after PR #5 merged — so the Phase A export manifest (`render_manifest`, `render_events_jsonl`, `saga-events.jsonl`/`saga-manifest.json` in the bundle) is already present. Do **not** run HEAD-detaching git commands; after each commit verify `git rev-parse --abbrev-ref HEAD` is `feature/okf-import`.

**Commit convention:** Conventional Commits, imperative mood, English. **Do not add a `Co-Authored-By: Claude` trailer.**

**Design spec:** `docs/superpowers/specs/2026-06-17-okf-faithful-round-trip-design.md` (§3 flow, §5 trust restore, §6 restore order + seeding, §7 robustness/dedup, §8 round-trip equality, §9 tests).

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/saga/export/okf.py` | OKF render helpers | Extract `render_notes_suffix`; `render_concept` uses it (shared with the import strip). |
| `src/saga/imports/__init__.py` | New `imports` package | Create (empty). |
| `src/saga/imports/okf.py` | `OkfBundleImporter`, `ImportSummary`, concept parse helpers | Create. The import counterpart of `export/okf.py`. |
| `src/saga/storage/postgres.py` | System of record | Add `restore_events` (verbatim insert, idempotent by `event_id`). |
| `src/saga/pipeline/queue.py` | Job names | Add `INDEX_JOB`. |
| `src/saga/pipeline/tasks.py` | Worker jobs | Add `index_document` (non-LLM reindex). |
| `src/saga/pipeline/worker.py` | Worker wiring | Register `index_document` in `WorkerSettings.functions`. |
| `src/saga/api/routes/imports.py` | REST | Create `POST /import/okf`. |
| `src/saga/api/app.py` | App | Register the imports router. |
| `src/saga/scripts/import_okf.py` | CLI | Create `saga-import-okf` thin client. |
| `pyproject.toml` | Entry points | Add `saga-import-okf`; add the `ASYNC240` per-file ignore for the client. |
| `tests/imports/test_concept_parse.py` | Concept round-trip fidelity | Create. |
| `tests/imports/test_okf_importer.py` | Importer units | Create. |
| `tests/imports/test_okf_roundtrip.py` | Headline round-trip | Create. |
| `tests/test_storage_events.py` | `restore_events` | Add test (create file if absent). |
| `tests/test_pipeline_index_document.py` | `index_document` | Create. |
| `tests/test_api_import_okf.py` | Route | Create. |

---

## Task 1: Shared concept render/parse helpers (content round-trips bit-for-bit)

**Files:**
- Modify: `src/saga/export/okf.py`
- Create: `src/saga/imports/__init__.py`, `src/saga/imports/okf.py`
- Test: `tests/imports/test_concept_parse.py`

- [ ] **Step 1: Write the failing test**

Create `tests/imports/test_concept_parse.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from saga.core.models import Document, Note
from saga.export.okf import render_concept, render_notes_suffix
from saga.imports.okf import split_frontmatter, strip_notes_suffix


def _doc(**kw: object) -> Document:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    base: dict[str, object] = {
        "document_id": "d1",
        "title": "Rechnung ACME",
        "filename": "rechnung.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 10,
        "content_hash": "h",
        "minio_object": "saga-originals/d1",
        "summary": "One invoice.",
        "content_markdown": "# Heading\n\nBody text with a -- dash.",
        "created_at": now,
        "updated_at": now,
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_render_notes_suffix_empty_and_nonempty() -> None:
    assert render_notes_suffix([]) == ""
    assert render_notes_suffix(["a", "b"]) == "\n## Notes\n\n- a\n- b\n"


def test_concept_content_round_trips_with_notes() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    doc = _doc(
        notes=[
            Note(note_id="n1", content="Check me", created_at=now, updated_at=now),
            Note(note_id="n2", content="And me", created_at=now, updated_at=now),
        ]
    )
    text = render_concept(doc, store_name="saga", public_base_url=None)
    fm, body_section = split_frontmatter(text)
    assert fm["saga_id"] == "d1"
    note_contents = [n["content"] for n in fm["saga_notes"]]
    assert strip_notes_suffix(body_section, note_contents) == doc.content_markdown


def test_concept_content_round_trips_without_notes() -> None:
    doc = _doc()
    text = render_concept(doc, store_name="saga", public_base_url=None)
    fm, body_section = split_frontmatter(text)
    assert fm.get("saga_notes") == []
    assert strip_notes_suffix(body_section, []) == doc.content_markdown


def test_split_frontmatter_no_frontmatter_returns_empty() -> None:
    fm, body = split_frontmatter("just text, no fence")
    assert fm == {}
    assert body == "just text, no fence"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_concept_parse.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'saga.imports'` / `cannot import name 'render_notes_suffix'`.

- [ ] **Step 3: Extract `render_notes_suffix` in `src/saga/export/okf.py`**

The current `render_concept` is:

```python
def render_concept(document: Document, *, store_name: str, public_base_url: str | None) -> str:
    """Render a concept file: YAML frontmatter, the markdown body, and an optional Notes section."""
    block = yaml.safe_dump(
        _frontmatter(document, store_name=store_name, public_base_url=public_base_url),
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    parts = [f"---\n{block}\n---\n"]
    body = document.content_markdown or ""
    if body:
        parts.append(f"\n{body}\n")
    if document.notes:
        notes = "\n".join(f"- {n.content}" for n in document.notes)
        parts.append(f"\n## Notes\n\n{notes}\n")
    return "".join(parts)
```

Add a new helper directly above it, and rewrite the notes part to use it:

```python
def render_notes_suffix(note_contents: list[str]) -> str:
    """Render the trailing ``## Notes`` section appended to a concept body.

    Returns ``""`` when there are no notes. The import side reconstructs this exact
    string from ``saga_notes`` and strips it to recover ``content_markdown`` bit-for-bit,
    so the two sides MUST stay in lockstep — that is why this is one shared function.
    """
    if not note_contents:
        return ""
    notes = "\n".join(f"- {content}" for content in note_contents)
    return f"\n## Notes\n\n{notes}\n"


def render_concept(document: Document, *, store_name: str, public_base_url: str | None) -> str:
    """Render a concept file: YAML frontmatter, the markdown body, and an optional Notes section."""
    block = yaml.safe_dump(
        _frontmatter(document, store_name=store_name, public_base_url=public_base_url),
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    parts = [f"---\n{block}\n---\n"]
    body = document.content_markdown or ""
    if body:
        parts.append(f"\n{body}\n")
    parts.append(render_notes_suffix([n.content for n in document.notes]))
    return "".join(parts)
```

- [ ] **Step 4: Create the imports package with the parse helpers**

Create `src/saga/imports/__init__.py`:

```python
"""OKF bundle import (the round-trip counterpart of ``saga.export``)."""
```

Create `src/saga/imports/okf.py`:

```python
"""OKF bundle import. See docs/superpowers/specs/2026-06-17-okf-faithful-round-trip-design.md.

Reads an extracted OKF bundle directory and restores it into the SAGA system of record.
A bundle with ``saga-manifest.json`` is restored faithfully (documents, folders, doc-types,
memberships, events); a foreign OKF bundle is re-enriched through the normal pipeline.
FastAPI-independent; the REST route extracts the uploaded ``.tar.gz`` and calls the importer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from saga.export.okf import render_notes_suffix

if TYPE_CHECKING:
    pass


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a concept file into ``(frontmatter dict, body section)``.

    The body section is everything after the closing ``---`` fence — i.e. the
    ``\\n<body>\\n`` plus an optional notes suffix that ``render_concept`` produced.
    A file without a leading ``---`` fence yields ``({}, text)``.
    """
    if not text.startswith("---\n"):
        return {}, text
    rest = text[len("---\n") :]
    end = rest.find("\n---\n")
    if end == -1:
        return {}, text
    block = rest[:end]
    body_section = rest[end + len("\n---\n") :]
    data = yaml.safe_load(block)
    if not isinstance(data, dict):
        return {}, body_section
    return data, body_section


def strip_notes_suffix(body_section: str, note_contents: list[str]) -> str:
    """Recover ``content_markdown`` from a concept body section.

    Reverses ``render_concept``'s body assembly: removes the exact rendered notes suffix
    (when there are notes) and the single leading/trailing newline that wrapped the body.
    """
    suffix = render_notes_suffix(note_contents)
    section = body_section
    if suffix and section.endswith(suffix):
        section = section[: -len(suffix)]
    if section.startswith("\n") and section.endswith("\n"):
        return section[1:-1]
    return section
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/imports/test_concept_parse.py -v -p no:cacheprovider --no-cov`
Expected: PASS (4 passed). Also run `uv run pytest tests/export/test_okf_render.py -p no:cacheprovider --no-cov` to confirm the `render_concept` refactor did not change existing output (the pre-existing concept test still passes).

- [ ] **Step 6: Self-check lint/types**

Run: `uv run ruff check src/saga/export/okf.py src/saga/imports tests/imports/test_concept_parse.py --fix` then `uv run ruff format src/saga/export/okf.py src/saga/imports tests/imports/test_concept_parse.py` then `uv run mypy src/saga/export/okf.py src/saga/imports`. All clean.

- [ ] **Step 7: Commit**

```bash
git add src/saga/export/okf.py src/saga/imports tests/imports/test_concept_parse.py
git commit -m "feat: add shared OKF concept notes-suffix render + parse helpers"
```

---

## Task 2: `PostgresStore.restore_events`

**Files:**
- Modify: `src/saga/storage/postgres.py`
- Test: `tests/test_storage_events.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_storage_events.py` (create the file with the header below if it does not exist):

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


def _event(event_id: str, *, folder_id: str | None = None) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id=event_id,
        category=EventCategory.AUDIT,
        event_type=EventType.FOLDER_CREATED,
        folder_id=folder_id,
        recorded_at=now,
        actor="system",
        summary=f"event {event_id}",
    )


async def test_restore_events_inserts_verbatim_and_skips_existing(store: PostgresStore) -> None:
    await store.append_event(_event("e1"))

    restored = await store.restore_events([_event("e1"), _event("e2"), _event("e3")])

    assert restored == 2  # e1 already exists -> skipped
    all_events = await TimelineService(store).query(EventQuery(limit=100))
    assert {e.event_id for e in all_events} == {"e1", "e2", "e3"}


async def test_restore_events_empty_is_noop(store: PostgresStore) -> None:
    assert await store.restore_events([]) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage_events.py -k restore_events -v -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: 'PostgresStore' object has no attribute 'restore_events'`.

- [ ] **Step 3: Implement `restore_events`**

Add this method to `PostgresStore` in `src/saga/storage/postgres.py`, directly below the existing `append_event` method (`EventRow` and `select` are already imported and used by `append_event`):

```python
    async def restore_events(self, events: list[Event]) -> int:
        """Insert *events* verbatim, skipping any whose ``event_id`` already exists.

        Used by the OKF import to restore the timeline exactly. Idempotent by
        ``event_id`` (re-importing the same bundle inserts nothing). Returns the number
        of events actually inserted.
        """
        if not events:
            return 0
        async with self._sessions()() as session, session.begin():
            ids = [e.event_id for e in events]
            existing = {
                row[0]
                for row in (
                    await session.execute(select(EventRow.id).where(EventRow.id.in_(ids)))
                ).all()
            }
            restored = 0
            for event in events:
                if event.event_id in existing:
                    continue
                session.add(
                    EventRow(
                        id=event.event_id,
                        category=str(event.category),
                        event_type=str(event.event_type),
                        document_id=event.document_id,
                        folder_id=event.folder_id,
                        occurred_at=event.occurred_at,
                        recorded_at=event.recorded_at,
                        actor=event.actor,
                        summary=event.summary,
                        confidence=event.confidence,
                        dedupe_key=event.dedupe_key,
                        details=dict(event.details),
                    )
                )
                restored += 1
            return restored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage_events.py -k restore_events -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/storage/postgres.py tests/test_storage_events.py --fix && uv run mypy src/saga/storage/postgres.py`

```bash
git add src/saga/storage/postgres.py tests/test_storage_events.py
git commit -m "feat: add PostgresStore.restore_events (verbatim, idempotent by event_id)"
```

---

## Task 3: `index_document` worker job (non-LLM reindex)

**Files:**
- Modify: `src/saga/pipeline/queue.py`, `src/saga/pipeline/tasks.py`, `src/saga/pipeline/worker.py`
- Test: `tests/test_pipeline_index_document.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pipeline_index_document.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Chunk, Document, DocumentStatus
from saga.pipeline.tasks import index_document
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeEmbedder


class _Chunker:
    def split(self, text: str) -> list[str]:
        return [block for block in text.split("\n\n") if block.strip()]


class _Projection:
    """Projection double implementing the three methods index_chunks calls."""

    def __init__(self) -> None:
        self.projected: dict[str, Document] = {}
        self.chunks: dict[str, int] = {}

    async def project_document(
        self,
        document: Document,
        *,
        folder_ancestor_ids: list[str],
        summary_embedding: list[float] | None = None,
    ) -> None:
        self.projected[document.document_id] = document

    async def delete_chunks(self, document_id: str) -> None:
        self.chunks.pop(document_id, None)

    async def index_chunks(self, chunks: list[Chunk]) -> int:
        for chunk in chunks:
            self.chunks[chunk.document_id] = self.chunks.get(chunk.document_id, 0) + 1
        return len(chunks)


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def test_index_document_projects_and_chunks_without_llm(store: PostgresStore) -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    await store.create_document(
        Document(
            document_id="d1",
            title="Doc",
            filename="d.md",
            mime_type="text/markdown",
            size_bytes=20,
            content_hash="h",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            summary="A summary.",
            content_markdown="Para one.\n\nPara two.",
            created_at=now,
            updated_at=now,
        )
    )
    projection = _Projection()
    ctx: dict[str, Any] = {
        "db": store,
        "opensearch": projection,
        "embedder": FakeEmbedder(),
        "chunker": _Chunker(),
    }

    await index_document(ctx, "d1")

    assert "d1" in projection.projected
    assert projection.chunks["d1"] == 2
```

NOTE: the test defines its own `_Projection` double because the shared `tests/conftest.py:InMemoryProjection` only implements `project_document`/`delete_document` — it lacks `index_chunks`/`delete_chunks`, which the real `index_chunks` stage (run by `index_document`) calls. `_Projection` implements all three. `FakeEmbedder.embed(list[str]) -> list[list[float]]` returns one vector per text, so `embedder.embed([summary])[0]` is the summary vector.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline_index_document.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `cannot import name 'index_document' from 'saga.pipeline.tasks'`.

- [ ] **Step 3: Add the `INDEX_JOB` name**

In `src/saga/pipeline/queue.py`, below the existing `INGEST_JOB`:

```python
#: Name of the ingestion job (must match the worker function name).
INGEST_JOB = "ingest_document"

#: Name of the non-LLM reindex job (must match the worker function name).
INDEX_JOB = "index_document"
```

- [ ] **Step 4: Implement `index_document` in `tasks.py`**

Add this function to `src/saga/pipeline/tasks.py`, after `ingest_document` (the module already imports `bind_correlation_id`, `index_chunks`, `NotFoundError`, the `PostgresStore`/`OpenSearchStore`/`EmbeddingProvider`/`MarkdownChunker` types, and `_log`):

```python
async def index_document(ctx: dict[str, Any], document_id: str) -> None:
    """Re-index a document without re-running the LLM stages (used by the OKF import).

    Embeds the already-stored summary and runs ``index_chunks`` (OpenSearch document
    projection + chunk vectors). Unlike ``ingest_document`` it does not convert, classify,
    extract, summarise, or place — so restored metadata and content/timeline events are
    preserved exactly.
    """
    bind_correlation_id(document_id)
    db: PostgresStore = ctx["db"]
    opensearch: OpenSearchStore = ctx["opensearch"]
    embedder: EmbeddingProvider = ctx["embedder"]
    chunker: MarkdownChunker = ctx["chunker"]

    document = await db.get_document(document_id)
    if document is None:
        raise NotFoundError(f"Document '{document_id}' was not found before indexing.")

    summary_vector: list[float] = []
    if document.summary:
        vectors = await embedder.embed([document.summary])
        summary_vector = vectors[0] if vectors else []

    await index_chunks(
        document_id=document_id,
        summary_vector=summary_vector,
        db=db,
        opensearch=opensearch,
        chunker=chunker,
        embedder=embedder,
    )
    _log.info("index_complete", document_id=document_id)
```

If any of `OpenSearchStore`, `EmbeddingProvider`, or `MarkdownChunker` is **not** already imported in `tasks.py`, add the missing import next to the existing pipeline-type imports (check the top of the file first). `index_chunks` is already imported (used by `ingest_document`).

- [ ] **Step 5: Register the job in the worker**

In `src/saga/pipeline/worker.py`, change the import and the `functions` list:

```python
from saga.pipeline.tasks import index_document, ingest_document
```

```python
    functions: ClassVar[list[Any]] = [ingest_document, index_document]
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_pipeline_index_document.py -v -p no:cacheprovider --no-cov`
Expected: PASS.

- [ ] **Step 7: Self-check + commit**

Run: `uv run ruff check src/saga/pipeline tests/test_pipeline_index_document.py --fix && uv run mypy src/saga/pipeline/tasks.py src/saga/pipeline/worker.py`

```bash
git add src/saga/pipeline/queue.py src/saga/pipeline/tasks.py src/saga/pipeline/worker.py tests/test_pipeline_index_document.py
git commit -m "feat: add index_document worker job for non-LLM reindex"
```

---

## Task 4: Importer skeleton — manifest/events load, doc-type + folder restore

**Files:**
- Modify: `src/saga/imports/okf.py`
- Test: `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/imports/test_okf_importer.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.config import AppConfig
from saga.core.models import Event, EventCategory, EventType
from saga.imports.okf import OkfBundleImporter
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
    # Second run reuses, creates nothing.
    _, created2, reused2 = await importer._restore_doc_types(doc_types)
    assert created2 == 0 and reused2 == 2


async def test_restore_folders_topological_with_id_map(store: PostgresStore) -> None:
    importer = _importer(store)
    folders = [
        {"id": "c", "name": "2026", "parent_id": "p", "description": None,
         "emoji": None, "metadata": {}},
        {"id": "p", "name": "Finanzen", "parent_id": None, "description": "Money",
         "emoji": "💰", "metadata": {"color": "green"}},
    ]
    id_map, created, reused = await importer._restore_folders(folders)
    assert created == 2 and reused == 0
    parent = await store.get_folder(id_map["p"])
    child = await store.get_folder(id_map["c"])
    assert parent is not None and child is not None
    assert parent.name == "Finanzen" and parent.emoji == "💰"
    assert child.parent_id == id_map["p"]
    # Idempotent: same parent+name reused, map points at the existing rows.
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_okf_importer.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `cannot import name 'OkfBundleImporter'`.

- [ ] **Step 3: Implement the importer skeleton**

Add to `src/saga/imports/okf.py`. First extend the imports at the top of the file:

```python
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from saga.core.logging import get_logger
from saga.core.models import Document, DocumentStatus, Event, ExtractedValue
from saga.export.okf import render_notes_suffix
from saga.pipeline.queue import INDEX_JOB, INGEST_JOB
from saga.scripts.layout import backup_basename

if TYPE_CHECKING:
    from saga.core.config import AppConfig

_log = get_logger("saga.import")
```

(Keep the `split_frontmatter` / `strip_notes_suffix` functions from Task 1.) Then add the summary model and the importer class:

```python
class ImportSummary(BaseModel):
    """What an OKF import did (the REST response body)."""

    documents_imported: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    folders_created: int = 0
    folders_reused: int = 0
    doc_types_created: int = 0
    doc_types_reused: int = 0
    events_restored: int = 0
    events_skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class DocumentSink(BaseModel):
    """Marker for the store operations the importer needs (documented for readers)."""


class OkfBundleImporter:
    """Restores an extracted OKF bundle into the SAGA system of record."""

    def __init__(self, *, db: Any, minio: Any, queue: Any, config: AppConfig) -> None:
        self._db = db
        self._minio = minio
        self._queue = queue
        self._config = config

    # --- bundle file access ------------------------------------------------ #

    @staticmethod
    def _find(bundle_dir: Path, name: str) -> Path | None:
        return next(iter(sorted(bundle_dir.rglob(name), key=lambda p: len(p.parts))), None)

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any] | None:
        path = self._find(bundle_dir, "saga-manifest.json")
        if path is None:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def _load_events(self, bundle_dir: Path) -> list[Event]:
        path = self._find(bundle_dir, "saga-events.jsonl")
        if path is None:
            return []
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                events.append(Event.model_validate_json(stripped))
        return events

    # --- doc-types --------------------------------------------------------- #

    async def _restore_doc_types(
        self, doc_types: list[dict[str, Any]]
    ) -> tuple[dict[str, str], int, int]:
        """Ensure each manifest doc-type; return (name->new id, created, reused)."""
        existing = {dt.name for dt in await self._db.list_doc_types()}
        name_to_id: dict[str, str] = {}
        created = reused = 0
        for dt in doc_types:
            name = dt["name"]
            if name in existing:
                reused += 1
            else:
                created += 1
                existing.add(name)
            ensured = await self._db.ensure_doc_type(
                name=name, description=dt.get("description"), emoji=dt.get("emoji")
            )
            name_to_id[name] = ensured.doc_type_id
        return name_to_id, created, reused

    # --- folders ----------------------------------------------------------- #

    async def _restore_folders(
        self, folders: list[dict[str, Any]]
    ) -> tuple[dict[str, str], int, int]:
        """Create folders parents-first; return (source id->new id, created, reused).

        Idempotent by ``(parent_id, name)``: an existing folder is reused and its id is
        mapped. A folder whose parent id is unknown is created at the root (logged).
        """
        existing = await self._db.list_folders()
        by_key: dict[tuple[str | None, str], str] = {
            (f.parent_id, f.name): f.folder_id for f in existing
        }
        id_map: dict[str, str] = {}
        created = reused = 0
        pending = list(folders)
        progressed = True
        while pending and progressed:
            progressed = False
            still: list[dict[str, Any]] = []
            for f in pending:
                src_parent = f.get("parent_id")
                if src_parent is None:
                    new_parent: str | None = None
                elif src_parent in id_map:
                    new_parent = id_map[src_parent]
                else:
                    still.append(f)
                    continue
                created_now, reused_now, new_id = await self._ensure_folder(f, new_parent, by_key)
                created += created_now
                reused += reused_now
                id_map[f["id"]] = new_id
                progressed = True
            pending = still
        for f in pending:  # unresolved parents -> attach to root, non-fatal
            _log.warning("okf_import_orphan_folder", folder=f.get("name"), parent=f.get("parent_id"))
            created_now, reused_now, new_id = await self._ensure_folder(f, None, by_key)
            created += created_now
            reused += reused_now
            id_map[f["id"]] = new_id
        return id_map, created, reused

    async def _ensure_folder(
        self,
        f: dict[str, Any],
        new_parent: str | None,
        by_key: dict[tuple[str | None, str], str],
    ) -> tuple[int, int, str]:
        key = (new_parent, f["name"])
        if key in by_key:
            return 0, 1, by_key[key]
        folder = await self._db.create_folder(
            name=f["name"],
            description=f.get("description"),
            parent_id=new_parent,
            metadata=f.get("metadata") or {},
            emoji=f.get("emoji"),
        )
        by_key[key] = folder.folder_id
        return 1, 0, folder.folder_id
```

NOTE: delete the placeholder `class DocumentSink` if you find it adds nothing — it is only a readability marker and YAGNI says drop it. (Listed here so the engineer does not invent a sink type; the importer talks to the store via duck-typed `db`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/imports/test_okf_importer.py -v -p no:cacheprovider --no-cov`
Expected: PASS (3 passed).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/imports tests/imports/test_okf_importer.py --fix && uv run mypy src/saga/imports`

```bash
git add src/saga/imports/okf.py tests/imports/test_okf_importer.py
git commit -m "feat: add OkfBundleImporter manifest/event load + doc-type/folder restore"
```

---

## Task 5: Document trust-restore

**Files:**
- Modify: `src/saga/imports/okf.py`
- Test: `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/imports/test_okf_importer.py`:

```python
from saga.core.models import Document, FolderRef, Note
from saga.export.okf import render_concept
from saga.pipeline.queue import INDEX_JOB


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
    # doc-type + folder must exist first (with the source ids used in the concept).
    doctype_ids, _, _ = await importer._restore_doc_types(
        [{"id": "dt-src", "name": "invoice", "description": "A bill.", "emoji": "📄"}]
    )
    folder_map, _, _ = await importer._restore_folders(
        [{"id": "f-src", "name": "Finanzen", "parent_id": None,
          "description": "Money", "emoji": "💰", "metadata": {}}]
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


async def test_restore_document_skips_existing_saga_id(tmp_path: Path, store: PostgresStore) -> None:
    importer = OkfBundleImporter(
        db=store, minio=InMemoryBinaryStore(), queue=FakeQueue(), config=AppConfig()
    )
    importer._config.dedup.on_duplicate = "reject"
    folder_map, _, _ = await importer._restore_folders(
        [{"id": "f-src", "name": "Finanzen", "parent_id": None,
          "description": None, "emoji": None, "metadata": {}}]
    )
    concept = tmp_path / "Rechnung-ACME__doc-1.md"
    concept.write_text(_concept_text("f-src"), encoding="utf-8")
    await importer._restore_document(concept, doctype_ids={}, folder_map=folder_map)

    result = await importer._restore_document(concept, doctype_ids={}, folder_map=folder_map)
    assert result == "skipped"
```

NOTE: there is **no** `store.list_document_notes` — `get_document(id)` returns a `Document` with its `.notes` populated (this is how the `/documents/{id}/notes` route reads them). `get_document_folders` does exist on `PostgresStore`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_okf_importer.py -k restore_document -v -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: 'OkfBundleImporter' object has no attribute '_restore_document'`.

- [ ] **Step 3: Implement `_restore_document` and a datetime helper**

Add to `src/saga/imports/okf.py`. First a module-level helper near the parse helpers:

```python
def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
```

Then this method on `OkfBundleImporter`:

```python
    async def _restore_document(
        self,
        concept_path: Path,
        *,
        doctype_ids: dict[str, str],
        folder_map: dict[str, str],
    ) -> str:
        """Restore one concept file into the store. Returns "imported" or "skipped"."""
        text = concept_path.read_text(encoding="utf-8")
        fm, body_section = split_frontmatter(text)
        saga_id = fm.get("saga_id")
        if not saga_id:
            raise ValueError(f"Concept '{concept_path.name}' has no saga_id.")

        existing = await self._db.get_document(saga_id)
        if existing is not None:
            # On a saga_id collision, 'replace' overwrites in place; 'reject' and 'allow'
            # both skip (a preserved primary key cannot be duplicated). See spec §7.
            if self._config.dedup.on_duplicate == "replace":
                await self._db.delete_document(saga_id)
            else:
                return "skipped"

        note_contents = [n["content"] for n in fm.get("saga_notes", [])]
        content_markdown = strip_notes_suffix(body_section, note_contents) or None
        mime_type = fm.get("saga_mime_type") or "application/octet-stream"

        data = self._read_original(concept_path, fm, saga_id)
        if data is not None:
            minio_object = await self._minio.put_object(saga_id, data, mime_type)
        else:
            data = (content_markdown or "").encode("utf-8")
            minio_object = await self._minio.put_object(saga_id, data, "text/markdown")

        now = datetime.now(UTC)
        document = Document(
            document_id=saga_id,
            title=fm.get("title") or saga_id,
            filename=fm.get("saga_filename") or "",
            mime_type=mime_type,
            size_bytes=int(fm.get("saga_size_bytes") or len(data)),
            content_hash=fm.get("saga_content_hash") or hashlib.sha256(data).hexdigest(),
            minio_object=minio_object,
            status=DocumentStatus(fm.get("saga_status", "ready")),
            content_markdown=content_markdown,
            doc_type_id=doctype_ids.get(fm.get("type")),
            summary=fm.get("description"),
            extracted_values=[ExtractedValue(**v) for v in fm.get("saga_extracted_values", [])],
            created_at=_parse_dt(fm.get("saga_created_at")) or now,
            updated_at=_parse_dt(fm.get("timestamp")) or now,
        )
        await self._db.create_document(document)

        for content in note_contents:
            await self._db.add_document_note(saga_id, content)

        refs = fm.get("saga_folders", [])
        folder_ids = [folder_map[r["id"]] for r in refs if r.get("id") in folder_map]
        primary = next(
            (folder_map[r["id"]] for r in refs if r.get("primary") and r.get("id") in folder_map),
            None,
        )
        if folder_ids:
            await self._db.set_document_folders(
                saga_id, folder_ids=folder_ids, primary_id=primary
            )

        await self._queue.enqueue_job(INDEX_JOB, saga_id)
        return "imported"

    def _read_original(self, concept_path: Path, fm: dict[str, Any], saga_id: str) -> bytes | None:
        """Return the original binary next to the concept, or None to use the markdown body."""
        suffix = Path(fm.get("saga_filename") or "").suffix
        if not suffix:
            return None
        base = backup_basename(saga_id, fm.get("title") or saga_id)
        original = concept_path.with_name(f"{base}{suffix}")
        return original.read_bytes() if original.exists() else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/imports/test_okf_importer.py -k restore_document -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/imports tests/imports/test_okf_importer.py --fix && uv run mypy src/saga/imports`

```bash
git add src/saga/imports/okf.py tests/imports/test_okf_importer.py
git commit -m "feat: add OKF document trust-restore (preserve id, content, notes, memberships)"
```

---

## Task 6: Event restore (folder-id remap + idempotency)

**Files:**
- Modify: `src/saga/imports/okf.py`
- Test: `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/imports/test_okf_importer.py`:

```python
from saga.events import EventQuery, TimelineService


async def test_restore_events_remaps_folder_id(store: PostgresStore) -> None:
    importer = _importer(store)
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folder_map = {"f-src": "f-new"}
    # f-new must exist for the audit event's folder_id to be meaningful; create it.
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_okf_importer.py -k restore_events -v -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: ... '_restore_events'`.

- [ ] **Step 3: Implement `_restore_events`**

Add this method to `OkfBundleImporter`:

```python
    async def _restore_events(
        self, events: list[Event], folder_map: dict[str, str]
    ) -> tuple[int, int]:
        """Restore events verbatim, remapping ``folder_id`` via the folder id map.

        ``None`` folder_ids (content/document-scoped events) stay ``None``. Returns
        (restored, skipped); skipped counts events whose ``event_id`` already exists.
        """
        remapped = [
            event.model_copy(
                update={
                    "folder_id": folder_map.get(event.folder_id)
                    if event.folder_id is not None
                    else None
                }
            )
            for event in events
        ]
        restored = await self._db.restore_events(remapped)
        return restored, len(remapped) - restored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/imports/test_okf_importer.py -k restore_events -v -p no:cacheprovider --no-cov`
Expected: PASS.

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/imports tests/imports/test_okf_importer.py --fix && uv run mypy src/saga/imports`

```bash
git add src/saga/imports/okf.py tests/imports/test_okf_importer.py
git commit -m "feat: add OKF event restore with folder-id remap"
```

---

## Task 7: Foreign-bundle re-enrich (directory-tree folders + frontmatter seeding)

**Files:**
- Modify: `src/saga/imports/okf.py`
- Test: `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/imports/test_okf_importer.py`:

```python
from saga.pipeline.queue import INGEST_JOB


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
    assert {await_name(store, fid) for fid in dir_to_id.values()} == {"Taxes"}

    result = await importer._reenrich_concept(root / "Taxes" / "note.md", dir_to_id)
    assert result == "imported"

    # A new document exists, seeded from frontmatter, with a re-enrich job queued.
    docs, _ = await store.list_documents(page=1, page_size=10)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "A Foreign Bill"
    assert doc.doc_type == "invoice"  # seeded -> ensure_doc_type
    assert doc.summary == "Imported note."
    assert any(job == INGEST_JOB for job, _ in queue.jobs)


async def await_name(store: PostgresStore, folder_id: str) -> str:
    folder = await store.get_folder(folder_id)
    assert folder is not None
    return folder.name
```

NOTE: confirm `store.list_documents(page=..., page_size=...)` returns `(list[Document], int)` (it backs `GET /documents`). If the signature differs, adjust the call.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_okf_importer.py -k reenrich -v -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: ... '_restore_foreign_folders'`.

- [ ] **Step 3: Implement the foreign-bundle methods**

Add these methods to `OkfBundleImporter`:

```python
    async def _restore_foreign_folders(self, root: Path) -> dict[Path, str]:
        """Rebuild folders from the bundle directory tree (each dir with index.md).

        Returns a map ``{directory path -> new folder id}``. The root index.md is the
        bundle index, not a folder. Directories are processed shallow-first so a parent
        folder exists before its children.
        """
        dir_to_id: dict[Path, str] = {}
        for index_file in sorted(root.rglob("index.md"), key=lambda p: len(p.parts)):
            directory = index_file.parent
            if directory == root:
                continue
            parent_id = dir_to_id.get(directory.parent)
            folder = await self._db.create_folder(name=directory.name, parent_id=parent_id)
            dir_to_id[directory] = folder.folder_id
        return dir_to_id

    async def _reenrich_concept(self, concept_path: Path, dir_to_id: dict[Path, str]) -> str:
        """Store a foreign concept (seeding title/type/description) and enqueue ingest."""
        fm, body_section = split_frontmatter(concept_path.read_text(encoding="utf-8"))
        content = body_section.strip("\n")
        title = fm.get("title") or concept_path.stem
        doc_type = fm.get("type")

        doc_type_id: str | None = None
        if doc_type and doc_type != "document":
            ensured = await self._db.ensure_doc_type(name=doc_type)
            doc_type_id = ensured.doc_type_id

        new_id = uuid.uuid4().hex
        data = content.encode("utf-8")
        minio_object = await self._minio.put_object(new_id, data, "text/markdown")
        now = datetime.now(UTC)
        document = Document(
            document_id=new_id,
            title=title,
            filename=f"{title}.md",
            mime_type="text/markdown",
            size_bytes=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
            minio_object=minio_object,
            status=DocumentStatus.PENDING,
            content_markdown=content,
            doc_type_id=doc_type_id,
            summary=fm.get("description"),
            created_at=now,
            updated_at=now,
        )
        await self._db.create_document(document)

        folder_id = dir_to_id.get(concept_path.parent)
        if folder_id is not None:
            await self._db.set_document_folders(new_id, folder_ids=[folder_id], primary_id=folder_id)

        await self._queue.enqueue_job(INGEST_JOB, new_id)
        return "imported"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/imports/test_okf_importer.py -k reenrich -v -p no:cacheprovider --no-cov`
Expected: PASS.

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/imports tests/imports/test_okf_importer.py --fix && uv run mypy src/saga/imports`

```bash
git add src/saga/imports/okf.py tests/imports/test_okf_importer.py
git commit -m "feat: add OKF foreign-bundle re-enrich with frontmatter seeding"
```

---

## Task 8: Importer orchestration (`run`) + robustness + summary

**Files:**
- Modify: `src/saga/imports/okf.py`
- Test: `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/imports/test_okf_importer.py`:

```python
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
                    {"id": "f-src", "name": "Finanzen", "parent_id": None,
                     "description": "Money", "emoji": "💰", "metadata": {}}
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/imports/test_okf_importer.py -k "run_faithful or run_foreign" -v -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: ... 'run'`.

- [ ] **Step 3: Implement `run`, `_concept_files`, `_bundle_root`**

Add these methods to `OkfBundleImporter`:

```python
    @staticmethod
    def _concept_files(root: Path) -> list[Path]:
        """All concept markdown files under *root* (excluding the OKF reserved files)."""
        return sorted(
            p for p in root.rglob("*.md") if p.name not in {"index.md", "log.md"}
        )

    def _bundle_root(self, bundle_dir: Path, manifest_path: Path | None) -> Path:
        if manifest_path is not None:
            return manifest_path.parent
        index = self._find(bundle_dir, "index.md")
        return index.parent if index is not None else bundle_dir

    async def run(self, bundle_dir: Path) -> ImportSummary:
        """Restore a bundle directory; never aborts on a single bad file (spec §7)."""
        summary = ImportSummary()
        manifest_path = self._find(bundle_dir, "saga-manifest.json")
        root = self._bundle_root(bundle_dir, manifest_path)

        if manifest_path is not None:
            manifest = self._load_manifest(bundle_dir) or {}
            doctype_ids, summary.doc_types_created, summary.doc_types_reused = (
                await self._restore_doc_types(manifest.get("doc_types", []))
            )
            folder_map, summary.folders_created, summary.folders_reused = (
                await self._restore_folders(manifest.get("folders", []))
            )
            for concept in self._concept_files(root):
                try:
                    result = await self._restore_document(
                        concept, doctype_ids=doctype_ids, folder_map=folder_map
                    )
                    if result == "imported":
                        summary.documents_imported += 1
                    else:
                        summary.documents_skipped += 1
                except Exception as exc:  # noqa: BLE001 - per-file robustness (spec §7)
                    summary.documents_failed += 1
                    summary.errors.append(f"{concept.name}: {exc}")
                    _log.warning("okf_import_document_failed", concept=concept.name, error=str(exc))
            try:
                summary.events_restored, summary.events_skipped = await self._restore_events(
                    self._load_events(bundle_dir), folder_map
                )
            except Exception as exc:  # noqa: BLE001 - event restore is best-effort
                summary.errors.append(f"events: {exc}")
                _log.warning("okf_import_events_failed", error=str(exc))
        else:
            dir_to_id = await self._restore_foreign_folders(root)
            summary.folders_created = len(dir_to_id)
            for concept in self._concept_files(root):
                try:
                    await self._reenrich_concept(concept, dir_to_id)
                    summary.documents_imported += 1
                except Exception as exc:  # noqa: BLE001 - per-file robustness (spec §7)
                    summary.documents_failed += 1
                    summary.errors.append(f"{concept.name}: {exc}")
                    _log.warning("okf_import_reenrich_failed", concept=concept.name, error=str(exc))
        return summary
```

NOTE: confirm `# noqa: BLE001` is not stripped by ruff. The Phase A reviewer found `BLE001` is **not** in the project's ruff select, so an unused `# noqa: BLE001` is itself flagged. If `uv run ruff check` complains about the noqa being unused, remove the `# noqa: BLE001` comments (keep the broad `except Exception` — it is intentional per spec §7, and the project's ruff config does not forbid it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/imports/test_okf_importer.py -v -p no:cacheprovider --no-cov`
Expected: PASS (all importer tests).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/imports tests/imports/test_okf_importer.py --fix && uv run mypy src/saga/imports`

```bash
git add src/saga/imports/okf.py tests/imports/test_okf_importer.py
git commit -m "feat: add OkfBundleImporter.run orchestration with per-file robustness"
```

---

## Task 9: `POST /import/okf` route + `saga-import-okf` client

**Files:**
- Create: `src/saga/api/routes/imports.py`, `src/saga/scripts/import_okf.py`
- Modify: `src/saga/api/app.py`, `pyproject.toml`
- Test: `tests/test_api_import_okf.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_import_okf.py`:

```python
"""API test for POST /import/okf (round-trips a bundle the exporter produced)."""

from __future__ import annotations

import io
import tarfile
import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from saga.api.app import create_app
from saga.api.dependencies import Services
from saga.core.config import AppConfig
from saga.core.models import Document, DocumentStatus
from saga.events import EventRecorder, TimelineService
from saga.export.okf import OkfBundleBuilder
from saga.storage.postgres import PostgresStore
from tests.conftest import FakeQueue, InMemoryBinaryStore

TEST_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", TEST_TOKEN)
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


@pytest_asyncio.fixture
async def store() -> AsyncIterator[PostgresStore]:
    tmp = Path(tempfile.mkdtemp()) / "okf-import-api.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", poolclass=NullPool)
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    try:
        yield s
    finally:
        await s.close()
        tmp.unlink(missing_ok=True)


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.security.bearer_tokens = TEST_TOKEN
    return cfg


@pytest.fixture
def services(store: PostgresStore, config: AppConfig) -> Services:
    return Services(
        config=config,
        db=store,
        opensearch=None,  # type: ignore[arg-type]
        minio=InMemoryBinaryStore(),
        queue=FakeQueue(),
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(store),
        timeline=TimelineService(store),
    )


async def _bundle_bytes(store: PostgresStore, minio: InMemoryBinaryStore) -> bytes:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folder = await store.create_folder(name="Finanzen", description="Money", emoji="💰")
    await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")
    doc = await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung",
            filename="r.md",
            mime_type="text/markdown",
            size_bytes=6,
            content_hash="h",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            doc_type="invoice",
            summary="One invoice.",
            content_markdown="# Body",
            created_at=now,
            updated_at=now,
        )
    )
    await store.set_document_folders("d1", folder_ids=[folder.folder_id], primary_id=folder.folder_id)
    builder = OkfBundleBuilder(
        db=store, minio=minio, timeline=TimelineService(store),
        store_name="saga", public_base_url=None, with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    return buf.getvalue()


async def test_import_okf_restores_bundle(store: PostgresStore, services: Services) -> None:
    # Build a bundle from a source store, then import it into a *fresh* store via the API.
    src_engine = create_async_engine("sqlite+aiosqlite://")
    src = PostgresStore(config=None, engine=src_engine)  # type: ignore[arg-type]
    await src.bootstrap()
    bundle = await _bundle_bytes(src, InMemoryBinaryStore())

    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.post(
            "/import/okf",
            headers=AUTH,
            files={"file": ("bundle.tar.gz", bundle, "application/gzip")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["documents_imported"] == 1
    assert body["folders_created"] == 1
    assert await store.get_document("d1") is not None


async def test_import_okf_requires_auth(services: Services) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.post("/import/okf").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_import_okf.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — 404 (route not registered) / import error.

- [ ] **Step 3: Create the route**

Create `src/saga/api/routes/imports.py`:

```python
"""Import endpoint: restore an uploaded OKF .tar.gz bundle (faithful round-trip)."""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from saga.api.dependencies import AuthDep, ServicesDep
from saga.imports.okf import ImportSummary, OkfBundleImporter

router = APIRouter(prefix="/import", tags=["import"], dependencies=[AuthDep])


@router.post("/okf", summary="Import an OKF .tar.gz bundle (faithful round-trip)")
async def import_okf(
    services: ServicesDep,
    file: Annotated[UploadFile, File(description="The OKF .tar.gz bundle to import.")],
) -> ImportSummary:
    data = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "bundle"
        extract_dir.mkdir()
        with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:gz") as tar:
            tar.extractall(extract_dir, filter="data")
        importer = OkfBundleImporter(
            db=services.db,
            minio=services.minio,
            queue=services.queue,
            config=services.config,
        )
        return await importer.run(extract_dir)
```

Replace the `__import__("io").BytesIO(data)` hack with a top-level `import io` and `io.BytesIO(data)` — it is written inline here only to keep the import list explicit; add `import io` to the import block and use `io.BytesIO(data)`.

- [ ] **Step 4: Register the router in `app.py`**

In `src/saga/api/app.py`, add `imports` to the routes import (line 21) and include the router next to the others (after `export`):

```python
from saga.api.routes import doctypes, documents, export, folders, imports, llm, search, timeline
```

```python
    app.include_router(export.router)
    app.include_router(imports.router)
```

- [ ] **Step 5: Run the route test**

Run: `uv run pytest tests/test_api_import_okf.py -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed).

- [ ] **Step 6: Create the thin client**

Create `src/saga/scripts/import_okf.py`:

```python
"""Thin REST client: upload an OKF bundle to /import/okf.

Usage:
    saga-import-okf --token <token> --bundle ./bundle.tar.gz
    saga-import-okf --token <token> --dir ./extracted-bundle   # tars the dir first
"""

from __future__ import annotations

import argparse
import asyncio
import io
import tarfile
from pathlib import Path

import httpx

from saga.core.logging import configure_logging, get_logger

_log = get_logger("saga.import.client")


def _tar_directory(directory: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(directory, arcname=directory.name)
    return buf.getvalue()


async def _run(*, base_url: str, token: str, bundle: Path | None, directory: Path | None) -> None:
    if bundle is not None:
        data = bundle.read_bytes()
    elif directory is not None:
        data = _tar_directory(directory)
    else:  # pragma: no cover - argparse guarantees one is set
        raise SystemExit("Provide --bundle or --dir.")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=300.0) as client:
        response = await client.post(
            "/import/okf", files={"file": ("bundle.tar.gz", data, "application/gzip")}
        )
        response.raise_for_status()
        _log.info("okf_import_done", summary=response.json())


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description="Import an OKF bundle into SAGA.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bundle", type=Path, help="A .tar.gz bundle to upload.")
    group.add_argument("--dir", type=Path, dest="directory", help="A bundle directory to tar.")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(
        _run(base_url=args.base_url, token=args.token, bundle=args.bundle, directory=args.directory)
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Register the entry point + ruff ignore in `pyproject.toml`**

In `[project.scripts]` add (next to `saga-export-okf`):

```toml
saga-import-okf = "saga.scripts.import_okf:main"
```

In the per-file ruff ignores (next to the `export_okf.py` entry), add:

```toml
"src/saga/scripts/import_okf.py" = ["ASYNC240"]
```

- [ ] **Step 8: Self-check + commit**

Run: `uv run ruff check src/saga tests/test_api_import_okf.py --fix && uv run mypy src/saga/api/routes/imports.py src/saga/scripts/import_okf.py && uv run pytest tests/test_api_import_okf.py -p no:cacheprovider --no-cov`

```bash
git add src/saga/api/routes/imports.py src/saga/api/app.py src/saga/scripts/import_okf.py pyproject.toml tests/test_api_import_okf.py
git commit -m "feat: add POST /import/okf route and saga-import-okf client"
```

---

## Task 10: Headline round-trip test + final gates

**Files:**
- Create: `tests/imports/test_okf_roundtrip.py`

- [ ] **Step 1: Write the round-trip test**

Create `tests/imports/test_okf_roundtrip.py`:

```python
"""Headline guarantee (spec §8/§10): export -> import reproduces the same state."""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.config import AppConfig
from saga.core.models import Document, DocumentStatus, Event, EventCategory, EventType, ExtractedValue
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


async def _seed(store: PostgresStore) -> dict[str, str]:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 1, tzinfo=UTC)
    finanzen = await store.create_folder(name="Finanzen", description="Money", emoji="💰")
    y2026 = await store.create_folder(name="2026", parent_id=finanzen.folder_id)
    await store.create_doc_type(name="invoice", description="A bill.", emoji="📄")
    doc = await store.create_document(
        Document(
            document_id="d1",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="abc123",
            minio_object="saga-originals/d1",
            status=DocumentStatus.READY,
            doc_type="invoice",
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
    return {"finanzen": finanzen.folder_id, "y2026": y2026.folder_id}


async def test_export_then_import_reproduces_state(source: PostgresStore) -> None:
    await _seed(source)
    src_minio = InMemoryBinaryStore()
    builder = OkfBundleBuilder(
        db=source, minio=src_minio, timeline=TimelineService(source),
        store_name="saga", public_base_url=None, with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)

    target = await _fresh_store()
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

    # Document: exact restore by preserved id.
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

    # Folder tree: structural equality (names + parent names).
    folders = {f.name: f for f in await target.list_folders()}
    assert set(folders) == {"Finanzen", "2026"}
    assert folders["Finanzen"].emoji == "💰"
    assert folders["2026"].parent_id == folders["Finanzen"].folder_id

    # Membership: document in the structurally-same folder, primary preserved.
    refs = await target.get_document_folders("d1")
    assert [r.folder_id for r in refs] == [folders["2026"].folder_id]
    assert refs[0].is_primary is True

    # Doc-types: name + description + emoji.
    doc_types = {dt.name: dt for dt in await target.list_doc_types()}
    assert doc_types["invoice"].description == "A bill." and doc_types["invoice"].emoji == "📄"

    # Events: exact by event_id; audit folder_id points at the structurally-same folder.
    events = {e.event_id: e for e in await TimelineService(target).query(EventQuery(limit=100))}
    assert set(events) == {"e-audit", "e-content"}
    assert events["e-audit"].folder_id == folders["Finanzen"].folder_id
    assert events["e-content"].folder_id is None
    assert events["e-content"].event_type == EventType.DATED_FACT
```

NOTE: this test asserts the §8 equality contract. Notes are read via `restored.notes` (no `list_document_notes` method exists). `get_document_folders`, `list_folders`, and `list_doc_types` are confirmed present on `PostgresStore`.

- [ ] **Step 2: Run the round-trip test**

Run: `uv run pytest tests/imports/test_okf_roundtrip.py -v -p no:cacheprovider --no-cov`
Expected: PASS. If `content_markdown` mismatches, the bug is in the shared notes-suffix strip (Task 1) — fix there, not by loosening the assertion.

- [ ] **Step 3: Run lint, type-check, and the FULL test suite**

Run:
```bash
uv run ruff check . --fix
uv run ruff format .
uv run mypy
uv run pytest
```
Expected: ruff clean; mypy no errors; all tests pass with coverage ≥ 80% on core packages. If `mypy` flags the duck-typed `db`/`minio`/`queue` in the importer (`Any`), that is acceptable here (the importer is deliberately decoupled like `OkfBundleBuilder`); only fix genuine type errors.

- [ ] **Step 4: Run the mandatory Docker build gate**

From the workspace root `d:\Projekte\Archiv` (Bash): `cd /d/Projekte/Archiv && docker compose build api worker`
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add tests/imports/test_okf_roundtrip.py
git commit -m "test: add OKF export->import round-trip equality test"
```

---

## Self-Review

**1. Spec coverage (against `2026-06-17-okf-faithful-round-trip-design.md`):**
- §3 `POST /import/okf` + extract-to-temp + importer → Task 9. `OkfBundleImporter` detects SAGA vs foreign by `saga-manifest.json` → Task 8 `run`. New non-LLM `index_document` job → Task 3. Thin client → Task 9. ✓
- §5 trust restore (frontmatter → Document; `document_id`←`saga_id`; `content_markdown` exact-strip via shared helper; binary original-or-markdown fallback; memberships from `saga_folders` remapped; notes) → Tasks 1, 5. ✓
- §6 faithful order doc-types → folders (topological + id map, idempotent by parent+name) → documents → events (verbatim, folder_id remap, idempotent by event_id) → index; foreign re-enrich with `title`/`type`/`description` seeding → Tasks 4, 5, 6, 7, 8. ✓
- §7 robustness (per-file try/except, continue, error list), import summary, dedup (`saga_id` then policy; `allow`→`reject` on id collision) → Tasks 5, 8. ✓
- §8 round-trip equality (documents, folder tree, doc-types, events; structural ids) → Task 10. ✓
- §9 tests: manifest/event load, folder topo + reuse, document trust-restore incl. Notes-strip, event remap + idempotency, foreign routing, route test, headline round-trip, `index_document` job → Tasks 1–10. ✓
- §10 phasing: this is Phase B; Phase A already merged. ✓

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Two readability notes are explicit instructions to *remove* scaffolding (`DocumentSink`, the inline `__import__` hack, possibly-unused `# noqa: BLE001`), not placeholders. Every code step has complete code.

**3. Type/name consistency:** `OkfBundleImporter(db, minio, queue, config)` used identically in every task and the route. Method names — `_load_manifest`, `_load_events`, `_restore_doc_types` (→ name→id map), `_restore_folders` (→ source-id→new-id map), `_ensure_folder`, `_restore_document`, `_read_original`, `_restore_events`, `_restore_foreign_folders`, `_reenrich_concept`, `_concept_files`, `_bundle_root`, `run` — are consistent across definition and call sites. `ImportSummary` field names match the route response and the test assertions. `INDEX_JOB`/`INGEST_JOB` imported from `saga.pipeline.queue`. `render_notes_suffix` is the single shared function used by both `render_concept` (append) and `strip_notes_suffix` (strip). Store methods (`restore_events`, `create_folder`, `ensure_doc_type`, `create_document`, `add_document_note`, `set_document_folders`, `delete_document`, `get_document`, `list_folders`, `list_doc_types`, `get_folder`) match the signatures confirmed during planning.

**Interfaces verified during planning (post-write fixes applied):** Task 3 uses its own `_Projection` double (the shared `InMemoryProjection` lacks `index_chunks`/`delete_chunks`); notes are read via `Document.notes` from `get_document` (no `list_document_notes` exists); `get_document_folders`, `list_folders`, `list_doc_types`, and `list_documents(*, page, page_size)` are confirmed on `PostgresStore`; `FakeEmbedder`/`InMemoryBinaryStore`/`FakeQueue` shapes match the tests; `put_object` returns `saga-originals/<id>`.
