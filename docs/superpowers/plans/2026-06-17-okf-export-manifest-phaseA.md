# OKF Export Manifest (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `OkfBundleBuilder` to also emit a machine-readable `saga-manifest.json` (folders + doc-types) and `saga-events.jsonl` (all events) at the bundle root, so a SAGA OKF bundle can later be imported back into the exact same state.

**Architecture:** Two pure render helpers (`render_manifest`, `render_events_jsonl`) turn store objects into deterministic JSON / JSONL strings. `OkfBundleBuilder.write_bundle` fetches doc-types alongside folders, pages all events through the existing `TimelineReader.query`, and writes both files with the existing deterministic `_add` (mtime=0). OKF consumers ignore non-markdown files, so the bundle stays a valid OKF bundle. No REST route change — the route delegates to `write_bundle`.

**Tech Stack:** Python 3.12, Pydantic v2 (`model_dump(mode="json")`), `tarfile`, `json`, pytest + pytest-asyncio (`asyncio_mode=auto`), SQLAlchemy async over `sqlite+aiosqlite` in tests.

**Baseline branch:** This phase **extends the OKF export builder**, which lives on `feature/okf-export` (commits `d4bc86e..50b8df0`, based on the merged Phase-1 tip `7377df6`). That branch already has `OkfBundleBuilder`, `query_events`, `TimelineService`, `list_folders`, and `list_doc_types`. Branch Phase A **from `feature/okf-export`** (stacked, since PR #3 is not yet merged). If PR #3 merges to `develop` before execution, rebase the Phase-A branch onto `develop` instead. Do **not** run HEAD-detaching git commands; verify `git rev-parse --abbrev-ref HEAD` stays on the feature branch after each commit.

**Commit convention:** Conventional Commits, imperative mood, English. **Do not add a `Co-Authored-By: Claude` trailer.**

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/saga/export/okf.py` | OKF bundle generation | Add `import json`; add `render_manifest` + `render_events_jsonl` pure helpers; add `list_doc_types` to the `DocumentSource` Protocol; add `_all_events()`; fetch `doc_types` and write the two files in `write_bundle`. |
| `tests/export/test_okf_render.py` | Pure render-helper tests | Add tests for `render_manifest` and `render_events_jsonl`. |
| `tests/export/test_okf_builder.py` | Builder integration tests | Add a test that the bundle contains a correct `saga-manifest.json` + `saga-events.jsonl`. |
| `tests/test_api_export_okf.py` | API route test | Add a test that the streamed `.tar.gz` contains both machine-readable files. |

---

## Task 1: `render_manifest` pure helper

**Files:**
- Modify: `src/saga/export/okf.py`
- Test: `tests/export/test_okf_render.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/export/test_okf_render.py`:

```python
import json
from datetime import UTC, datetime

from saga.core.models import DocType, Folder
from saga.export.okf import render_manifest


def test_render_manifest_maps_folders_and_doc_types() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    folders = [
        Folder(
            folder_id="f-root",
            name="Finanzen",
            description="Money",
            emoji="💰",
            parent_id=None,
            metadata={"color": "green"},
            created_at=now,
            updated_at=now,
        ),
        Folder(
            folder_id="f-child",
            name="2026",
            description=None,
            emoji=None,
            parent_id="f-root",
            metadata={},
            created_at=now,
            updated_at=now,
        ),
    ]
    doc_types = [
        DocType(
            doc_type_id="dt1",
            name="invoice",
            description="A bill.",
            emoji="📄",
            created_at=now,
            updated_at=now,
        )
    ]

    manifest = json.loads(render_manifest("saga", folders, doc_types))

    assert manifest["version"] == "1"
    assert manifest["store"] == "saga"
    assert manifest["folders"] == [
        {
            "id": "f-root",
            "name": "Finanzen",
            "parent_id": None,
            "description": "Money",
            "emoji": "💰",
            "metadata": {"color": "green"},
        },
        {
            "id": "f-child",
            "name": "2026",
            "parent_id": "f-root",
            "description": None,
            "emoji": None,
            "metadata": {},
        },
    ]
    assert manifest["doc_types"] == [
        {"id": "dt1", "name": "invoice", "description": "A bill.", "emoji": "📄"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_okf_render.py::test_render_manifest_maps_folders_and_doc_types -v`
Expected: FAIL with `ImportError: cannot import name 'render_manifest'`.

- [ ] **Step 3: Add `import json` to `src/saga/export/okf.py`**

The current import block (top of file) is:

```python
import io
import tarfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol

import yaml
```

Change it to add `json` (alphabetical with the stdlib block):

```python
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol

import yaml
```

Then extend the `TYPE_CHECKING` import to include `DocType` (it currently imports `Document, Event, Folder`):

```python
if TYPE_CHECKING:
    from saga.core.models import Document, DocType, Event, Folder
```

- [ ] **Step 4: Implement `render_manifest`**

Add this function to `src/saga/export/okf.py`, directly above `def render_concept(` (next to the other render helpers):

```python
def render_manifest(
    store_name: str, folders: list[Folder], doc_types: list[DocType]
) -> str:
    """Render the machine-readable ``saga-manifest.json`` (folders + doc-types).

    OKF consumers ignore non-markdown files; the SAGA import uses this for the exact
    folder-tree restore (and the source-folder-id -> new-folder-id map) and to restore
    doc-type descriptions/emoji that the concept frontmatter does not carry.
    """
    manifest = {
        "version": "1",
        "store": store_name,
        "folders": [
            {
                "id": f.folder_id,
                "name": f.name,
                "parent_id": f.parent_id,
                "description": f.description,
                "emoji": f.emoji,
                "metadata": f.metadata,
            }
            for f in folders
        ],
        "doc_types": [
            {
                "id": dt.doc_type_id,
                "name": dt.name,
                "description": dt.description,
                "emoji": dt.emoji,
            }
            for dt in doc_types
        ],
    }
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/export/test_okf_render.py::test_render_manifest_maps_folders_and_doc_types -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_render.py
git commit -m "feat: add render_manifest for OKF saga-manifest.json"
```

---

## Task 2: `render_events_jsonl` pure helper

**Files:**
- Modify: `src/saga/export/okf.py`
- Test: `tests/export/test_okf_render.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/export/test_okf_render.py`:

```python
from saga.core.models import Event, EventCategory, EventType
from saga.export.okf import render_events_jsonl


def test_render_events_jsonl_one_object_per_line() -> None:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    events = [
        Event(
            event_id="e1",
            category=EventCategory.AUDIT,
            event_type=EventType.FOLDER_CREATED,
            folder_id="f-root",
            recorded_at=now,
            actor="system",
            summary="Created folder Finanzen",
        ),
        Event(
            event_id="e2",
            category=EventCategory.CONTENT,
            event_type=EventType.DATED_FACT,
            document_id="d1",
            occurred_at=now,
            recorded_at=now,
            actor="llm",
            summary="Invoice dated 2026-05-01",
            confidence=0.9,
            details={"date": "2026-05-01"},
        ),
    ]

    text = render_events_jsonl(events)

    lines = text.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == events[0].model_dump(mode="json")
    assert json.loads(lines[1]) == events[1].model_dump(mode="json")
    # Trailing newline so the file is POSIX-clean and append-friendly.
    assert text.endswith("\n")


def test_render_events_jsonl_empty() -> None:
    assert render_events_jsonl([]) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_okf_render.py -k render_events_jsonl -v`
Expected: FAIL with `ImportError: cannot import name 'render_events_jsonl'`.

- [ ] **Step 3: Implement `render_events_jsonl`**

Add this function to `src/saga/export/okf.py`, directly below `render_manifest`:

```python
def render_events_jsonl(events: list[Event]) -> str:
    """Render ``saga-events.jsonl``: one ``Event.model_dump(mode="json")`` per line.

    JSONL keeps large event volumes streamable. An empty list yields an empty string.
    """
    return "".join(
        json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
        for event in events
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/export/test_okf_render.py -k render_events_jsonl -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_render.py
git commit -m "feat: add render_events_jsonl for OKF saga-events.jsonl"
```

---

## Task 3: Wire manifest + events into `write_bundle`

**Files:**
- Modify: `src/saga/export/okf.py` (`DocumentSource` Protocol, `write_bundle`, new `_all_events`)
- Test: `tests/export/test_okf_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/export/test_okf_builder.py` (the module already imports `io`, `tarfile`, `datetime`, `Document`, `Event`, `EventQuery`, `OkfBundleBuilder`, `PostgresStore` and defines the `store` fixture and `_Minio`):

```python
import json

from saga.core.models import EventCategory, EventType
from saga.events import TimelineService


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
            json.loads(line)["event_id"]
            for line in events_member.read().decode().splitlines()
        }
        assert event_ids == {"e-audit", "e-content"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/export/test_okf_builder.py::test_write_bundle_emits_manifest_and_events -v`
Expected: FAIL — `saga-manifest.json` not in `names` (and/or `AttributeError`/`TypeError` because `list_doc_types` is not yet on the Protocol / `_all_events` does not exist).

- [ ] **Step 3: Add `list_doc_types` to the `DocumentSource` Protocol**

In `src/saga/export/okf.py` the Protocol currently reads:

```python
class DocumentSource(Protocol):
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = ...
    ) -> tuple[list[Document], str | None]: ...
    async def list_folders(self) -> list[Folder]: ...
    async def parents_map(self) -> dict[str, str | None]: ...
```

Add a `list_doc_types` method:

```python
class DocumentSource(Protocol):
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = ...
    ) -> tuple[list[Document], str | None]: ...
    async def list_folders(self) -> list[Folder]: ...
    async def list_doc_types(self) -> list[DocType]: ...
    async def parents_map(self) -> dict[str, str | None]: ...
```

- [ ] **Step 4: Fetch doc-types and write the two files in `write_bundle`**

In `write_bundle`, the top currently is:

```python
        documents = await self._all_documents()
        folders = await self._db.list_folders()
        parents = await self._db.parents_map()
        path_by_id = _folder_paths(folders, parents)
        root = f"okf-{self._store_name}-{datetime.now(UTC):%Y%m%d_%H%M%S}"
```

Add the doc-types fetch:

```python
        documents = await self._all_documents()
        folders = await self._db.list_folders()
        doc_types = await self._db.list_doc_types()
        parents = await self._db.parents_map()
        path_by_id = _folder_paths(folders, parents)
        root = f"okf-{self._store_name}-{datetime.now(UTC):%Y%m%d_%H%M%S}"
```

Then, immediately **after** the root `index.md` is added (this block):

```python
        self._add(
            tar,
            f"{root}/index.md",
            render_index("Index", subfolders=root_subfolders, documents=[]),
        )
```

insert the manifest + events writes:

```python
        # Machine-readable extras for a faithful SAGA round-trip. OKF consumers ignore
        # non-markdown files; the SAGA import uses these for exact restore.
        # See docs/superpowers/specs/2026-06-17-okf-faithful-round-trip-design.md.
        self._add(
            tar,
            f"{root}/saga-manifest.json",
            render_manifest(self._store_name, folders, doc_types),
        )
        self._add(
            tar,
            f"{root}/saga-events.jsonl",
            render_events_jsonl(await self._all_events()),
        )
```

- [ ] **Step 5: Implement `_all_events`**

Add this method to `OkfBundleBuilder`, directly below the existing `_folder_events` method:

```python
    async def _all_events(self) -> list[Event]:
        """Page every event (audit + content, all folders) via the timeline read path.

        ``EventQuery`` with ``folder_id=None`` applies no folder/document filter, so the
        store returns all events; we page by offset until a short page.
        """
        out: list[Event] = []
        offset = 0
        while True:
            page = await self._timeline.query(
                EventQuery(limit=self._page_size, offset=offset)
            )
            out.extend(page)
            if len(page) < self._page_size:
                return out
            offset += self._page_size
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/export/test_okf_builder.py -v`
Expected: PASS (the new test plus the two pre-existing `write_bundle` tests).

- [ ] **Step 7: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_builder.py
git commit -m "feat: emit saga-manifest.json and saga-events.jsonl in OKF bundles"
```

---

## Task 4: Route-level assertion + final gates

**Files:**
- Test: `tests/test_api_export_okf.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_export_okf.py` (the module already imports `io`, `tarfile`, `TestClient`, `create_app`, and defines `store`, `services`, `_seed`, `AUTH`):

```python
async def test_export_okf_bundle_contains_machine_readable_files(
    store: PostgresStore,
    services: Services,
) -> None:
    await _seed(store)
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/export/okf", headers=AUTH)

    assert resp.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        root = names[0].split("/")[0]
        assert f"{root}/saga-manifest.json" in names
        assert f"{root}/saga-events.jsonl" in names
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_api_export_okf.py::test_export_okf_bundle_contains_machine_readable_files -v`
Expected: PASS (the route already delegates to `write_bundle`, which now writes both files — this test guards against regressions).

- [ ] **Step 3: Run lint, type-check, and the full test suite**

Run:
```bash
uv run ruff check . --fix
uv run ruff format .
uv run mypy
uv run pytest
```
Expected: ruff clean, mypy reports no errors, all tests pass (≥80% coverage on core packages maintained).

- [ ] **Step 4: Run the mandatory Docker build gate**

From the workspace root `d:\Projekte\Archiv` (PowerShell):
```powershell
docker compose build api worker
```
Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add tests/test_api_export_okf.py
git commit -m "test: assert OKF bundle ships saga-manifest.json and saga-events.jsonl"
```

---

## Self-Review

**1. Spec coverage (against `2026-06-17-okf-faithful-round-trip-design.md` §4 + §9):**
- §4 `saga-manifest.json` (`version`, `store`, `folders[id,name,parent_id,description,emoji,metadata]`, `doc_types[id,name,description,emoji]`) → Task 1 (`render_manifest`) + Task 3 (wired with real `list_folders`/`list_doc_types`). ✓
- §4 `saga-events.jsonl` (one `Event.model_dump(mode="json")` per line, all events via the no-filter query) → Task 2 (`render_events_jsonl`) + Task 3 (`_all_events`). ✓
- §4 "documents stay the OKF concept files; a bundle without the manifest is still valid OKF" → unchanged existing behavior; we only add files. ✓
- §9 Phase A tests (manifest folders incl. description/emoji; events serialized verbatim) → Task 3 integration test (asserts emoji, metadata, parent_id, doc-type emoji, both event ids). ✓
- §9 final gates (ruff, mypy, pytest, docker build) → Task 4. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✓

**3. Type consistency:** `render_manifest(store_name: str, folders: list[Folder], doc_types: list[DocType]) -> str` and `render_events_jsonl(events: list[Event]) -> str` are used with the same signatures in Task 3. `DocumentSource.list_doc_types() -> list[DocType]` matches `PostgresStore.list_doc_types`. `_all_events` uses `EventQuery(limit=..., offset=...)`, which exists. Manifest keys (`id`/`name`/`parent_id`/`description`/`emoji`/`metadata`; `id`/`name`/`description`/`emoji`) match `Folder`/`DocType` field reads. ✓
