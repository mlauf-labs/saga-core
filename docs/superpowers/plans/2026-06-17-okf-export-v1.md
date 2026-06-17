# OKF Export (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `GET /export/okf` endpoint that streams the whole SAGA archive as an Open Knowledge Format `.tar.gz` bundle (concept files with frontmatter + body, per-folder `index.md` and `log.md`), plus a thin REST client to download it.

**Architecture:** A FastAPI-independent `OkfBundleBuilder` (in `saga/export/okf.py`) turns the stores + `TimelineService` into bundle files; pure module-level render functions produce each file's text. A thin route builds the bundle into a `SpooledTemporaryFile` (tar.gz) and streams it. Generation is in-process so `log.md` can use `TimelineService` directly.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async ORM (asyncpg; aiosqlite in tests), PyYAML, `tarfile`, `uv`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-17-okf-export-design.md`.

**Conventions (`saga-core/CLAUDE.md`):** fully typed, `uv run mypy` strict; `uv run ruff check . --fix` + `uv run ruff format .`; tests ≥80% on core packages with external services mocked; config over constants; errors from `saga.core.errors`; structured logging via `get_logger`; English only. **Do NOT add a `Co-Authored-By` trailer to commits.** All paths are relative to `saga-core/`. Commit locally only (no push) unless told otherwise.

---

## Relevant existing code (read before starting)

- `src/saga/scripts/layout.py` — `sanitize_component`, `backup_relative_dir`, `backup_basename`, `original_filename`, `metadata_payload`. **Reuse** `sanitize_component` + `backup_basename`.
- `src/saga/scripts/backup.py` — the existing REST-client backup CLI (pattern to mirror for the thin client).
- `src/saga/api/routes/export.py` — the existing `/export/documents` route (add `/okf` here).
- `src/saga/storage/postgres.py` — `PostgresStore.scroll_documents(*, page_size, after_id=None) -> tuple[list[Document], str | None]`, `list_folders() -> list[Folder]`, `parents_map() -> dict[str, str | None]`.
- `src/saga/storage/minio.py` — `MinioStore.get_object(object_name) -> bytes` (the object name **is the document id**, as used in `pipeline/stages.py`).
- `src/saga/events/service.py` — `TimelineService.query(EventQuery) -> list[Event]`; `EventQuery(folder_id=..., include_subtree=False, limit=..., offset=...)`.
- `src/saga/core/models.py` — `Document` (`.document_id .title .filename .mime_type .size_bytes .content_hash .status .doc_type .doc_type_id .summary .extracted_values .folders .notes .content_markdown .created_at .updated_at`, property `.primary_folder_id`); `FolderRef(.folder_id .name .emoji .is_primary)`; `Folder(.folder_id .name .description .parent_id …)`; `ExtractedValue(.key .type .value .normalized .confidence)`; `Note(.content .created_at .updated_at)`; `Event(.category .event_type .summary .occurred_at .recorded_at …)`; `EventCategory`.
- `src/saga/api/dependencies.py` — `Services` has `db`, `minio`, `timeline` (`TimelineService | None`), `config`.

---

## File Structure

**New**
- `src/saga/export/__init__.py` — exports `OkfBundleBuilder`.
- `src/saga/export/okf.py` — narrow source protocols, pure render functions, `OkfBundleBuilder`.
- `src/saga/scripts/export_okf.py` — thin REST client CLI.
- `tests/export/__init__.py`, `tests/export/test_okf_render.py` — render-function unit tests.
- `tests/export/test_okf_builder.py` — `write_bundle` integration test (tar inspection).
- `tests/test_api_export_okf.py` — route test.

**Modify**
- `src/saga/core/config.py` — add `ExportConfig` + `export` field on `AppConfig`.
- `config/config.yaml` — add an `export:` section.
- `src/saga/api/routes/export.py` — add `GET /export/okf`.
- `pyproject.toml` — register the `saga-export-okf` console script (optional convenience).

---

## Task 1: ExportConfig

**Files:**
- Modify: `src/saga/core/config.py`, `config/config.yaml`
- Test: `tests/core/test_config_export.py`

- [ ] **Step 1: Write the failing test** — `tests/core/test_config_export.py`:

```python
import pytest

from saga.core.config import load_config


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", "t")
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "p")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "a")
    monkeypatch.setenv("MINIO_SECRET_KEY", "s")


def test_export_config_default_public_base_url_is_none() -> None:
    cfg = load_config()
    assert cfg.export.public_base_url is None
```

(If the project's config-test env fixture differs, mirror `tests/core/test_config_timeline.py`.)

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/core/test_config_export.py -v --no-cov` → `AttributeError: 'AppConfig' object has no attribute 'export'`.

- [ ] **Step 3: Add `ExportConfig`** in `src/saga/core/config.py`, following the sibling pattern (e.g. `TimelineConfig`). Add the model:

```python
class ExportConfig(BaseModel):
    """Tunables for the OKF export."""

    # Public base URL used to build resolvable OKF `resource` links
    # (e.g. https://saga.example.com). When unset, a saga:// URI is used.
    public_base_url: str | None = None
```

Add the field to `AppConfig` mirroring siblings (`Field(default_factory=ExportConfig)`):

```python
    export: ExportConfig = Field(default_factory=ExportConfig)
```

If `load_config()` enumerates top-level YAML keys in a tuple/list, add `"export"` to it (check how `"timeline"` was added).

- [ ] **Step 4: Add to `config/config.yaml`** (top-level, near `timeline:`):

```yaml
export:
  # Public base URL for resolvable OKF `resource` links; null → saga:// fallback.
  public_base_url: null
```

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/core/test_config_export.py -v --no-cov`.

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/core/config.py tests/core/test_config_export.py` and `uv run mypy src/saga/core/config.py`.

- [ ] **Step 7: Commit**

```bash
git add src/saga/core/config.py config/config.yaml tests/core/test_config_export.py
git commit -m "feat: add ExportConfig.public_base_url for OKF resource links"
```

---

## Task 2: `okf.py` skeleton + `resource_uri` + `render_concept`

**Files:**
- Create: `src/saga/export/__init__.py`, `src/saga/export/okf.py`, `tests/export/__init__.py`, `tests/export/test_okf_render.py`

- [ ] **Step 1: Write the failing test** — `tests/export/test_okf_render.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import yaml

from saga.core.models import Document, ExtractedValue, FolderRef, Note
from saga.export.okf import render_concept, resource_uri


def _doc(**kw: object) -> Document:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    base: dict[str, object] = dict(
        document_id="d1",
        title="Rechnung ACME",
        filename="rechnung.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        content_hash="h",
        minio_object="saga-originals/d1",
        doc_type="invoice",
        summary="One invoice.",
        content_markdown="# Body",
        created_at=now,
        updated_at=now,
    )
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_resource_uri_prefers_public_base_url() -> None:
    doc = _doc()
    assert resource_uri(doc, store_name="saga", public_base_url="https://x/") == (
        "https://x/documents/d1/file"
    )
    assert resource_uri(doc, store_name="saga", public_base_url=None) == (
        "saga://saga/documents/d1"
    )


def test_render_concept_frontmatter_body_and_notes() -> None:
    doc = _doc(
        doc_type=None,
        folders=[FolderRef(folder_id="f1", name="Finanzen", is_primary=True)],
        extracted_values=[ExtractedValue(key="total", type="money", value="9.99")],
        notes=[Note(note_id="n1", content="Check me", created_at=datetime(2026, 5, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 5, 1, tzinfo=UTC))],
    )
    text = render_concept(doc, store_name="saga", public_base_url=None)
    assert text.startswith("---\n")
    front, _, body = text.partition("\n---\n")
    fm = yaml.safe_load(front[len("---\n"):])
    assert fm["type"] == "document"          # fallback when doc_type is None
    assert fm["title"] == "Rechnung ACME"
    assert fm["resource"] == "saga://saga/documents/d1"
    assert fm["tags"] == ["Finanzen"]
    assert fm["saga_id"] == "d1"
    assert fm["saga_extracted_values"][0]["key"] == "total"
    assert fm["saga_notes"][0]["content"] == "Check me"
    assert "# Body" in body
    assert "## Notes" in body and "Check me" in body
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/export/test_okf_render.py -v --no-cov` → `ModuleNotFoundError: No module named 'saga.export'`.

- [ ] **Step 3: Create `src/saga/export/okf.py`** with the protocols, `resource_uri`, frontmatter, and `render_concept` (further render functions are added in later tasks):

```python
"""OKF bundle generation. See docs/superpowers/specs/2026-06-17-okf-export-design.md.

Turns the SAGA system of record into an Open Knowledge Format bundle: one concept file per
document (YAML frontmatter + markdown body + optional Notes), plus per-folder index.md and
log.md. FastAPI-independent; the REST route streams the builder's output as a .tar.gz.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import yaml

from saga.events import EventQuery

if TYPE_CHECKING:
    from saga.core.models import Document, Event, Folder


class DocumentSource(Protocol):
    async def scroll_documents(
        self, *, page_size: int, after_id: str | None = ...
    ) -> tuple[list[Document], str | None]: ...
    async def list_folders(self) -> list[Folder]: ...
    async def parents_map(self) -> dict[str, str | None]: ...


class BinaryReader(Protocol):
    async def get_object(self, object_name: str) -> bytes: ...


class TimelineReader(Protocol):
    async def query(self, q: EventQuery) -> list[Event]: ...


def resource_uri(document: Document, *, store_name: str, public_base_url: str | None) -> str:
    """Resolvable URL when a public base URL is configured, else an opaque saga:// URI."""
    if public_base_url:
        return f"{public_base_url.rstrip('/')}/documents/{document.document_id}/file"
    return f"saga://{store_name}/documents/{document.document_id}"


def _frontmatter(
    document: Document, *, store_name: str, public_base_url: str | None
) -> dict[str, object]:
    fm: dict[str, object] = {"type": document.doc_type or "document", "title": document.title}
    if document.summary:
        fm["description"] = document.summary
    fm["resource"] = resource_uri(document, store_name=store_name, public_base_url=public_base_url)
    tags = sorted({ref.name for ref in document.folders})
    if tags:
        fm["tags"] = tags
    fm["timestamp"] = document.updated_at.isoformat()
    fm["saga_id"] = document.document_id
    if document.doc_type_id:
        fm["saga_doc_type_id"] = document.doc_type_id
    fm["saga_content_hash"] = document.content_hash
    fm["saga_mime_type"] = document.mime_type
    fm["saga_size_bytes"] = document.size_bytes
    fm["saga_status"] = str(document.status)
    fm["saga_filename"] = document.filename
    fm["saga_created_at"] = document.created_at.isoformat()
    fm["saga_folders"] = [
        {"id": r.folder_id, "name": r.name, "primary": r.is_primary} for r in document.folders
    ]
    fm["saga_extracted_values"] = [
        {
            "key": v.key,
            "type": v.type,
            "value": v.value,
            "normalized": v.normalized,
            "confidence": v.confidence,
        }
        for v in document.extracted_values
    ]
    fm["saga_notes"] = [
        {
            "content": n.content,
            "created_at": n.created_at.isoformat(),
            "updated_at": n.updated_at.isoformat(),
        }
        for n in document.notes
    ]
    return fm


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

- [ ] **Step 4: Create `src/saga/export/__init__.py`:**

```python
"""OKF export subsystem."""

from __future__ import annotations

from saga.export.okf import OkfBundleBuilder

__all__ = ["OkfBundleBuilder"]
```

> Note: `OkfBundleBuilder` is added in Task 5. Until then, either omit this `__init__` import or add a placeholder class. **Recommended:** create `__init__.py` with the import in Task 5 instead; for Task 2 create an empty `src/saga/export/__init__.py` (`"""OKF export subsystem."""`). Use the empty form now.

Create `src/saga/export/__init__.py` as just:

```python
"""OKF export subsystem."""
```

And create `tests/export/__init__.py` (empty).

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/export/test_okf_render.py -v --no-cov`.

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/export tests/export` and `uv run mypy src/saga/export`.

- [ ] **Step 7: Commit**

```bash
git add src/saga/export/__init__.py src/saga/export/okf.py tests/export/__init__.py tests/export/test_okf_render.py
git commit -m "feat: add OKF concept-file rendering (frontmatter + body + notes)"
```

---

## Task 3: `render_index`

**Files:**
- Modify: `src/saga/export/okf.py`
- Test: `tests/export/test_okf_render.py` (append)

- [ ] **Step 1: Append the failing test:**

```python
from saga.export.okf import render_index


def test_render_index_lists_subfolders_and_documents() -> None:
    text = render_index(
        "Finanzen",
        subfolders=[("2026", "2026/index.md", "Year 2026")],
        documents=[("Rechnung ACME", "Rechnung-ACME__d1.md", "One invoice."),
                   ("Notiz", "Notiz__d2.md", None)],
    )
    assert text.startswith("# Finanzen")
    assert "## Subfolders" in text
    assert "* [2026](2026/index.md) — Year 2026" in text
    assert "## Documents" in text
    assert "* [Rechnung ACME](Rechnung-ACME__d1.md) — One invoice." in text
    assert "* [Notiz](Notiz__d2.md)" in text  # no description → no em dash suffix
    assert "Notiz__d2.md) —" not in text
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/export/test_okf_render.py -k render_index -v --no-cov` → `ImportError`.

- [ ] **Step 3: Add to `okf.py`:**

```python
def _bullets(entries: list[tuple[str, str, str | None]]) -> list[str]:
    lines: list[str] = []
    for text, href, desc in entries:
        suffix = f" — {desc}" if desc else ""
        lines.append(f"* [{text}]({href}){suffix}")
    return lines


def render_index(
    heading: str,
    *,
    subfolders: list[tuple[str, str, str | None]],
    documents: list[tuple[str, str, str | None]],
) -> str:
    """Render an OKF index.md (no frontmatter): heading + Subfolders + Documents bullet lists.

    Each entry is ``(link_text, bundle_relative_href, description_or_None)``.
    """
    lines: list[str] = [f"# {heading}", ""]
    if subfolders:
        lines += ["## Subfolders", *_bullets(subfolders), ""]
    if documents:
        lines += ["## Documents", *_bullets(documents), ""]
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/export/test_okf_render.py -k render_index -v --no-cov`.

- [ ] **Step 5: Lint/type** — `uv run ruff check src/saga/export && uv run mypy src/saga/export`.

- [ ] **Step 6: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_render.py
git commit -m "feat: add OKF index.md rendering"
```

---

## Task 4: `render_log`

**Files:**
- Modify: `src/saga/export/okf.py`
- Test: `tests/export/test_okf_render.py` (append)

- [ ] **Step 1: Append the failing test:**

```python
from saga.core.models import Event, EventCategory, EventType
from saga.export.okf import render_log


def _event(category: EventCategory, etype: EventType, *, occurred: str, recorded: str, summary: str) -> Event:
    return Event(
        event_id="e",
        category=category,
        event_type=etype,
        occurred_at=datetime.fromisoformat(occurred),
        recorded_at=datetime.fromisoformat(recorded),
        actor="pipeline",
        summary=summary,
    )


def test_render_log_groups_by_date_newest_first_with_category_tags() -> None:
    events = [
        _event(EventCategory.AUDIT, EventType.PLACEMENT,
               occurred="2026-06-13T10:00:00+00:00", recorded="2026-06-13T10:00:00+00:00",
               summary="Placed in 1 folder."),
        _event(EventCategory.CONTENT, EventType.APPOINTMENT,
               occurred="2026-05-01T00:00:00+00:00", recorded="2026-06-13T10:00:00+00:00",
               summary="Policy expiry."),
    ]
    text = render_log("Änderungsverlauf — Finanzen", events)
    assert text.startswith("# Änderungsverlauf — Finanzen")
    # audit grouped by recorded_at (2026-06-13), content grouped by occurred_at (2026-05-01)
    assert text.index("## 2026-06-13") < text.index("## 2026-05-01")  # newest first
    assert "* **[audit] placement** — Placed in 1 folder." in text
    assert "* **[content] appointment** — Policy expiry." in text
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/export/test_okf_render.py -k render_log -v --no-cov` → `ImportError`.

- [ ] **Step 3: Add to `okf.py`** (add `from saga.core.models import EventCategory` to the runtime imports — it is needed at runtime here):

```python
def render_log(heading: str, events: list[Event]) -> str:
    """Render an OKF log.md: date-grouped (newest first), each line tagged [category] type.

    Audit events group by ``recorded_at``; content events group by ``occurred_at``.
    *events* arrive newest-first (recorded_at desc); per-day order is preserved.
    """
    groups: dict[str, list[Event]] = {}
    for ev in events:
        when = ev.occurred_at if ev.category == EventCategory.CONTENT else ev.recorded_at
        day = (when or ev.recorded_at).date().isoformat()
        groups.setdefault(day, []).append(ev)
    lines: list[str] = [f"# {heading}", ""]
    for day in sorted(groups, reverse=True):
        lines.append(f"## {day}")
        lines += [f"* **[{ev.category}] {ev.event_type}** — {ev.summary}" for ev in groups[day]]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/export/test_okf_render.py -k render_log -v --no-cov`.

- [ ] **Step 5: Lint/type** — `uv run ruff check src/saga/export && uv run mypy src/saga/export`.

- [ ] **Step 6: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_render.py
git commit -m "feat: add OKF log.md rendering from timeline events"
```

---

## Task 5: `OkfBundleBuilder.write_bundle`

**Files:**
- Modify: `src/saga/export/okf.py`, `src/saga/export/__init__.py`
- Test: `tests/export/test_okf_builder.py`

- [ ] **Step 1: Write the failing test** — `tests/export/test_okf_builder.py`:

```python
from __future__ import annotations

import io
import tarfile
from datetime import UTC, datetime

import pytest
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
            document_id="",
            title="Rechnung ACME",
            filename="rechnung.pdf",
            mime_type="application/pdf",
            size_bytes=8,
            content_hash="h",
            minio_object="saga-originals/x",
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


def _names(tar: tarfile.TarFile) -> list[str]:
    return tar.getnames()


async def test_write_bundle_lays_out_index_and_concept_files(store: PostgresStore) -> None:
    await _seed(store)
    builder = OkfBundleBuilder(
        db=store, minio=_Minio(), timeline=_Timeline(),
        store_name="saga", public_base_url=None, with_originals=False,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = _names(tar)
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
        db=store, minio=_Minio(), timeline=_Timeline(),
        store_name="saga", public_base_url=None, with_originals=True,
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        assert any(n.endswith(".pdf") for n in tar.getnames())
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/export/test_okf_builder.py -v --no-cov` → `ImportError: cannot import name 'OkfBundleBuilder'`.

- [ ] **Step 3: Add the builder + helpers to `okf.py`** (add the runtime imports shown):

```python
import io
import tarfile
from pathlib import PurePosixPath

from saga.core.logging import get_logger
from saga.scripts.layout import backup_basename, sanitize_component

_log = get_logger("saga.export")


def _folder_paths(folders: list[Folder], parents: dict[str, str | None]) -> dict[str, list[str]]:
    names = {f.folder_id: f.name for f in folders}
    out: dict[str, list[str]] = {}
    for fid in names:
        chain: list[str] = []
        cur: str | None = fid
        while cur is not None and cur in names:
            chain.append(names[cur])
            cur = parents.get(cur)
        out[fid] = list(reversed(chain))
    return out


def _concept_filename(document: Document) -> str:
    return f"{backup_basename(document.document_id, document.title)}.md"


def _original_filename(document: Document) -> str:
    suffix = PurePosixPath(document.filename).suffix
    return f"{backup_basename(document.document_id, document.title)}{suffix}"


class OkfBundleBuilder:
    """Builds an OKF bundle into a tar archive (FastAPI-independent)."""

    def __init__(
        self,
        *,
        db: DocumentSource,
        minio: BinaryReader,
        timeline: TimelineReader,
        store_name: str,
        public_base_url: str | None,
        with_originals: bool = False,
        page_size: int = 200,
    ) -> None:
        self._db = db
        self._minio = minio
        self._timeline = timeline
        self._store_name = store_name
        self._public_base_url = public_base_url
        self._with_originals = with_originals
        self._page_size = page_size

    async def write_bundle(self, tar: tarfile.TarFile) -> None:
        documents = await self._all_documents()
        folders = await self._db.list_folders()
        parents = await self._db.parents_map()
        path_by_id = _folder_paths(folders, parents)
        root = f"okf-{self._store_name}-{datetime.now(UTC):%Y%m%d_%H%M%S}"

        children: dict[str | None, list[Folder]] = {}
        for f in folders:
            children.setdefault(f.parent_id, []).append(f)
        docs_by_folder: dict[str | None, list[Document]] = {}
        for d in documents:
            docs_by_folder.setdefault(d.primary_folder_id, []).append(d)

        has_unfiled = bool(docs_by_folder.get(None))
        top = sorted(children.get(None, []), key=lambda f: f.name)
        root_subfolders = [(f.name, f"{sanitize_component(f.name)}/index.md", f.description) for f in top]
        if has_unfiled:
            root_subfolders.append(("Unfiled", "_unfiled/index.md", None))
        self._add(tar, f"{root}/index.md", render_index("Index", subfolders=root_subfolders, documents=[]))

        for folder in folders:
            parts = [sanitize_component(p) for p in path_by_id[folder.folder_id]]
            base = f"{root}/{'/'.join(parts)}"
            heading = " / ".join(path_by_id[folder.folder_id])
            subs = [
                (c.name, f"{sanitize_component(c.name)}/index.md", c.description)
                for c in sorted(children.get(folder.folder_id, []), key=lambda f: f.name)
            ]
            fdocs = docs_by_folder.get(folder.folder_id, [])
            doc_entries = [(d.title, _concept_filename(d), d.summary) for d in fdocs]
            self._add(tar, f"{base}/index.md", render_index(heading, subfolders=subs, documents=doc_entries))

            events = await self._folder_events(folder.folder_id)
            if events:
                self._add(tar, f"{base}/log.md", render_log(f"Änderungsverlauf — {heading}", events))

            await self._write_documents(tar, base, fdocs)

        if has_unfiled:
            base = f"{root}/_unfiled"
            unfiled = docs_by_folder[None]
            entries = [(d.title, _concept_filename(d), d.summary) for d in unfiled]
            self._add(tar, f"{base}/index.md", render_index("Unfiled", subfolders=[], documents=entries))
            await self._write_documents(tar, base, unfiled)

    async def _write_documents(self, tar: tarfile.TarFile, base: str, docs: list[Document]) -> None:
        for d in docs:
            text = render_concept(d, store_name=self._store_name, public_base_url=self._public_base_url)
            self._add(tar, f"{base}/{_concept_filename(d)}", text)
            if self._with_originals:
                try:
                    data = await self._minio.get_object(d.document_id)
                except Exception as exc:  # noqa: BLE001 - a missing binary must not fail the export
                    _log.warning("okf_original_missing", document_id=d.document_id, error=str(exc))
                    continue
                self._add(tar, f"{base}/{_original_filename(d)}", data)

    async def _all_documents(self) -> list[Document]:
        out: list[Document] = []
        cursor: str | None = None
        while True:
            page, cursor = await self._db.scroll_documents(page_size=self._page_size, after_id=cursor)
            out.extend(page)
            if not cursor:
                return out

    async def _folder_events(self, folder_id: str) -> list[Event]:
        out: list[Event] = []
        offset = 0
        while True:
            page = await self._timeline.query(
                EventQuery(folder_id=folder_id, include_subtree=False, limit=self._page_size, offset=offset)
            )
            out.extend(page)
            if len(page) < self._page_size:
                return out
            offset += self._page_size

    @staticmethod
    def _add(tar: tarfile.TarFile, path: str, content: str | bytes) -> None:
        data = content.encode("utf-8") if isinstance(content, str) else content
        info = tarfile.TarInfo(name=path)
        info.size = len(data)
        info.mtime = 0  # deterministic, git-diffable bundles
        tar.addfile(info, io.BytesIO(data))
```

- [ ] **Step 4: Update `src/saga/export/__init__.py`** to export the builder:

```python
"""OKF export subsystem."""

from __future__ import annotations

from saga.export.okf import OkfBundleBuilder

__all__ = ["OkfBundleBuilder"]
```

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/export/test_okf_builder.py -v --no-cov` (and the render tests: `uv run pytest tests/export -v --no-cov`).

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/export tests/export && uv run mypy src/saga/export`.

- [ ] **Step 7: Commit**

```bash
git add src/saga/export/okf.py src/saga/export/__init__.py tests/export/test_okf_builder.py
git commit -m "feat: add OkfBundleBuilder.write_bundle (layout, index, log, originals)"
```

---

## Task 6: `GET /export/okf` route

**Files:**
- Modify: `src/saga/api/routes/export.py`
- Test: `tests/test_api_export_okf.py`

- [ ] **Step 1: Write the failing test** — `tests/test_api_export_okf.py`. Mirror the auth/env/Services setup of `tests/test_api_timeline.py` (file-backed sqlite store, `cfg.security.bearer_tokens`, Bearer header). Core assertions:

```python
import io
import tarfile

# (reuse the project's API test harness: required env vars, sqlite PostgresStore, a real
# EventRecorder/TimelineService on Services, create_app(config=cfg, services=services),
# TestClient, and a Bearer token — copy the pattern from tests/test_api_timeline.py.)


def test_export_okf_streams_a_bundle(client, seeded_document) -> None:
    resp = client.get("/export/okf", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gzip")
    with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
        names = tar.getnames()
        root = names[0].split("/")[0]
        assert f"{root}/index.md" in names
        assert any(n.endswith(".md") and "__" in n for n in names)  # a concept file
```

(Build `Services` with a real `TimelineService(store)` so the route's `services.timeline` is set; `minio` can be a stub whose `get_object` returns bytes since `with_originals` defaults false and won't be called.)

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/test_api_export_okf.py -v --no-cov` → 404 (route missing).

- [ ] **Step 3: Add the route to `src/saga/api/routes/export.py`.** Add imports at the top:

```python
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime
from tempfile import SpooledTemporaryFile

from fastapi import Query
from fastapi.responses import StreamingResponse

from saga.core.errors import SagaError
from saga.export import OkfBundleBuilder
```

Add the route:

```python
@router.get("/okf", summary="Export the whole archive as an OKF .tar.gz bundle")
async def export_okf(
    services: ServicesDep,
    with_originals: Annotated[bool, Query(description="Include original binaries.")] = False,
) -> StreamingResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    builder = OkfBundleBuilder(
        db=services.db,
        minio=services.minio,
        timeline=services.timeline,
        store_name=services.config.name,
        public_base_url=services.config.export.public_base_url,
        with_originals=with_originals,
    )
    tmp: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b")
    with tarfile.open(fileobj=tmp, mode="w:gz") as tar:
        await builder.write_bundle(tar)
    tmp.seek(0)

    def _stream() -> Iterator[bytes]:
        try:
            while chunk := tmp.read(64 * 1024):
                yield chunk
        finally:
            tmp.close()

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"okf-{services.config.name}-{stamp}.tar.gz"
    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

(`Annotated` is already imported in this file; keep imports sorted/ruff-clean.)

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/test_api_export_okf.py -v --no-cov`.

- [ ] **Step 5: Regression + lint/type** — `uv run pytest tests/test_api_export.py -v --no-cov` (existing export route still works), `uv run ruff check src/saga/api tests/test_api_export_okf.py`, `uv run mypy src/saga/api`.

- [ ] **Step 6: Commit**

```bash
git add src/saga/api/routes/export.py tests/test_api_export_okf.py
git commit -m "feat: add GET /export/okf streaming OKF bundle endpoint"
```

---

## Task 7: Thin client `scripts/export_okf.py`

**Files:**
- Create: `src/saga/scripts/export_okf.py`
- Modify: `pyproject.toml` (register console script)
- Test: `tests/test_export_okf_client.py`

- [ ] **Step 1: Write the failing test** — `tests/test_export_okf_client.py` (mock httpx; assert the bundle is written):

```python
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx
import pytest

from saga.scripts.export_okf import download_bundle


def _fake_targz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"# Index\n"
        info = tarfile.TarInfo("okf-saga-x/index.md")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


async def test_download_bundle_writes_file(tmp_path: Path) -> None:
    payload = _fake_targz()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/export/okf"
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, content=payload)

    out = tmp_path / "bundle.tar.gz"
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        await download_bundle(client, out=out, with_originals=False)
    assert out.read_bytes() == payload
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/test_export_okf_client.py -v --no-cov` → `ImportError`.

- [ ] **Step 3: Create `src/saga/scripts/export_okf.py`:**

```python
"""Thin REST client: download the OKF bundle from /export/okf and save it.

Usage:
    saga-export-okf --base-url http://localhost:8000 --token <token> --out bundle.tar.gz
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import httpx

from saga.core.logging import configure_logging, get_logger

_log = get_logger("saga.export.client")


async def download_bundle(
    client: httpx.AsyncClient, *, out: Path, with_originals: bool
) -> None:
    """GET /export/okf and write the .tar.gz to *out*."""
    params = {"with_originals": str(with_originals).lower()}
    response = await client.get("/export/okf", params=params)
    response.raise_for_status()
    out.write_bytes(response.content)
    _log.info("okf_bundle_saved", out=str(out), bytes=len(response.content))


async def _run(*, base_url: str, token: str, out: Path, with_originals: bool) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=300.0) as client:
        await download_bundle(client, out=out, with_originals=with_originals)


def main() -> None:  # pragma: no cover - thin CLI wrapper
    parser = argparse.ArgumentParser(description="Download the SAGA archive as an OKF bundle.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--token", required=True)
    parser.add_argument("--out", type=Path, default=Path("okf-bundle.tar.gz"))
    parser.add_argument("--with-originals", action="store_true")
    args = parser.parse_args()
    configure_logging()
    asyncio.run(
        _run(base_url=args.base_url, token=args.token, out=args.out, with_originals=args.with_originals)
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Register the console script in `pyproject.toml`** under `[project.scripts]` (next to the existing `saga-*` entries):

```toml
saga-export-okf = "saga.scripts.export_okf:main"
```

(Match the existing table's formatting; check how `saga-backup`/`saga-api` are registered.)

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/test_export_okf_client.py -v --no-cov`.

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/scripts/export_okf.py tests/test_export_okf_client.py && uv run mypy src/saga/scripts/export_okf.py`.

- [ ] **Step 7: Commit**

```bash
git add src/saga/scripts/export_okf.py pyproject.toml tests/test_export_okf_client.py
git commit -m "feat: add saga-export-okf thin client for the OKF bundle endpoint"
```

---

## Task 8: Full verification

**Files:** none.

- [ ] **Step 1:** `uv run ruff check . --fix && uv run ruff format .` → clean.
- [ ] **Step 2:** `uv run mypy` → `Success` (full run, includes tests).
- [ ] **Step 3:** `uv run pytest` → all pass, coverage ≥ 80%.
- [ ] **Step 4:** Mandatory Docker build (from workspace root `d:\Projekte\Archiv`): `docker compose build api worker` → exit 0.
- [ ] **Step 5:** Manual smoke (optional, if a stack is up): start the API, `saga-export-okf --token <t> --out /tmp/b.tar.gz`, then `tar tzf /tmp/b.tar.gz` and confirm `index.md`, a concept `*.md`, and (for a folder with events) `log.md`.

---

## Self-Review notes (already applied)

- **Spec coverage:** §3 endpoint+builder+client → Tasks 5/6/7; §4 layout+frontmatter → Tasks 2/5; §5 index/log → Tasks 3/4/5; §6 config/errors/determinism → Tasks 1/5 (mtime=0, sorted order, missing-binary skip); §7 testing → each task + Task 8. Out-of-scope items (content log.md, subtree, import) correctly absent.
- **Type consistency:** `render_concept`/`render_index`/`render_log`/`resource_uri` signatures match between definition and tests; `OkfBundleBuilder.__init__(db, minio, timeline, store_name, public_base_url, with_originals, page_size)` matches the route's construction; narrow protocols (`DocumentSource`/`BinaryReader`/`TimelineReader`) are satisfied by `PostgresStore`/`MinioStore`/`TimelineService`.
- **Known sharp edges for the implementer:** (a) the route test must mirror `tests/test_api_timeline.py`'s exact auth/env/Services harness; (b) `_all_documents` holds all `Document`s in memory — acceptable for v1, note for large archives; (c) confirm `[project.scripts]` formatting in `pyproject.toml`.

## Commit note

No `Co-Authored-By` trailer (per the user's standing preference / updated CLAUDE.md). Commit locally; do not push unless asked.
