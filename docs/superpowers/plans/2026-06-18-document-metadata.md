# Document Metadata Bag — Implementation Plan (saga-core)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a free-form, user/agent-editable, searchable `Document.metadata` (`dict[str,str]`)
that round-trips OKF bundles losslessly and is projected to OpenSearch for search + filtering.

**Architecture:** Postgres stays the system of record (new `documents.metadata` JSON column).
A shared `okf_keys` module defines the reserved-frontmatter contract used by both export and
import. OpenSearch gets a `metadata` (nested) + `metadata_text` (flattened) projection, mirroring
the existing `extracted_values` / `value_terms` precedent. Edits go through REST `PATCH`, the MCP
write tool, and (separate deliverable) the saga-ui document detail.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + Alembic, OpenSearch, MCP, pytest,
mypy-strict, ruff. Spec: `docs/superpowers/specs/2026-06-18-document-metadata-design.md`.

**Conventions:** every test is `async def` (pytest-asyncio auto mode); the `db` fixture is
in-memory SQLite (`tests/conftest.py`); run a single test with
`uv run pytest tests/<file>::<name> -q`. Final gates: `uv run ruff check . && uv run ruff format
--check . && uv run mypy && uv run pytest`.

---

### Task 1: Reserved-frontmatter contract (shared module)

**Files:**
- Create: `src/saga/okf_keys.py`
- Test: `tests/test_okf_keys.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_okf_keys.py
from saga.okf_keys import RESERVED_FRONTMATTER_KEYS, is_reserved_key, metadata_from_frontmatter


def test_reserved_set_contains_the_okf_standard_keys() -> None:
    assert {"type", "title", "description", "tags", "resource", "timestamp"} <= (
        RESERVED_FRONTMATTER_KEYS
    )


def test_is_reserved_key_covers_reserved_names_and_saga_prefix() -> None:
    assert is_reserved_key("type")
    assert is_reserved_key("saga_id")
    assert is_reserved_key("saga_anything")
    assert not is_reserved_key("project")
    assert not is_reserved_key("author")


def test_metadata_from_frontmatter_keeps_only_unreserved_keys_as_strings() -> None:
    fm = {
        "type": "invoice",
        "title": "X",
        "saga_id": "d1",
        "project": "Apollo",
        "priority": 3,          # coerced to str
        "approved": True,       # coerced to str
    }
    assert metadata_from_frontmatter(fm) == {
        "project": "Apollo",
        "priority": "3",
        "approved": "True",
    }
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_okf_keys.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'saga.okf_keys'`.

- [ ] **Step 3: Write the module**

```python
# src/saga/okf_keys.py
"""The OKF frontmatter key contract shared by export and import.

SAGA owns a fixed set of frontmatter keys (the OKF-standard ones it emits, plus everything
prefixed ``saga_``). Every *other* top-level key is free-form document metadata: emitted as a
top-level key on export and captured into ``Document.metadata`` on import. Keeping the rule in
one module guarantees export and import agree, so foreign bundles round-trip losslessly.
"""

from __future__ import annotations

from typing import Any

# The OKF-standard keys SAGA writes in concept frontmatter (export/okf.py:_frontmatter).
RESERVED_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {"type", "title", "description", "tags", "resource", "timestamp"}
)
SAGA_KEY_PREFIX = "saga_"


def is_reserved_key(key: str) -> bool:
    """True if *key* is SAGA-owned (a reserved OKF key or a ``saga_*`` key)."""
    return key in RESERVED_FRONTMATTER_KEYS or key.startswith(SAGA_KEY_PREFIX)


def metadata_from_frontmatter(frontmatter: dict[str, Any]) -> dict[str, str]:
    """Return the free-form metadata in *frontmatter*: every non-reserved key, value→str."""
    return {k: str(v) for k, v in frontmatter.items() if not is_reserved_key(k)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_okf_keys.py -q` — Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/saga/okf_keys.py tests/test_okf_keys.py
git commit -m "feat: shared OKF reserved-frontmatter key contract (FR-57)"
```

---

### Task 2: `Document.metadata` model field

**Files:**
- Modify: `src/saga/core/models.py:171` (the `Document` field block)
- Test: `tests/test_models.py` (create if absent) or append to an existing model test

- [ ] **Step 1: Write the failing test**

```python
# tests/test_document_metadata_model.py
from datetime import UTC, datetime
from saga.core.models import Document


def _doc(**kw: object) -> Document:
    base: dict[str, object] = {
        "document_id": "d1", "title": "t", "mime_type": "text/plain", "size_bytes": 1,
        "content_hash": "h", "minio_object": "o",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC), "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


def test_metadata_defaults_to_empty_dict() -> None:
    assert _doc().metadata == {}


def test_metadata_round_trips_string_values() -> None:
    assert _doc(metadata={"project": "Apollo"}).metadata == {"project": "Apollo"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_document_metadata_model.py -q`
Expected: FAIL — `metadata` not a field / unexpected keyword.

- [ ] **Step 3: Add the field** in `src/saga/core/models.py`, after `extracted_values` (line 171), before `folders`:

```python
    extracted_values: list[ExtractedValue] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    folders: list[FolderRef] = Field(default_factory=list)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_document_metadata_model.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saga/core/models.py tests/test_document_metadata_model.py
git commit -m "feat: add Document.metadata field"
```

---

### Task 3: Postgres column + migration + store CRUD

**Files:**
- Modify: `src/saga/storage/postgres.py` (DocumentRow ~line 138; `create_document` ~663;
  `update_document` ~780; `_load_document` return ~1268)
- Create: `migrations/versions/0005_add_document_metadata.py`
- Test: `tests/test_postgres_document_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_postgres_document_metadata.py
from datetime import UTC, datetime
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from saga.core.models import Document
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    s = PostgresStore(config=None, engine=create_async_engine("sqlite+aiosqlite://"))  # type: ignore[arg-type]
    await s.bootstrap()
    return s


def _doc(**kw: object) -> Document:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    base: dict[str, object] = {
        "document_id": "d1", "title": "t", "filename": "t.txt", "mime_type": "text/plain",
        "size_bytes": 1, "content_hash": "h", "minio_object": "o",
        "created_at": now, "updated_at": now,
    }
    base.update(kw)
    return Document(**base)  # type: ignore[arg-type]


async def test_create_and_get_persists_metadata(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"project": "Apollo"}))
    loaded = await store.get_document("d1")
    assert loaded is not None and loaded.metadata == {"project": "Apollo"}


async def test_update_replaces_metadata(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"a": "1"}))
    updated = await store.update_document("d1", metadata={"b": "2"})
    assert updated.metadata == {"b": "2"}


async def test_update_leaves_metadata_unchanged_when_omitted(store: PostgresStore) -> None:
    await store.create_document(_doc(metadata={"a": "1"}))
    updated = await store.update_document("d1", title="new title")
    assert updated.metadata == {"a": "1"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_postgres_document_metadata.py -q`
Expected: FAIL — `create_document` ignores metadata / `update_document` has no `metadata` arg.

- [ ] **Step 3a: Add the ORM column** in `DocumentRow` (after `extracted_values`, ~line 140):

```python
    extracted_values: Mapped[list[dict[str, Any]]] = mapped_column(
        _JSON, default=list, nullable=False
    )
    document_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _JSON, default=dict, nullable=False
    )
```

- [ ] **Step 3b: Persist on create** — in `create_document` (~line 675), add to the `DocumentRow(...)`:

```python
                extracted_values=[v.model_dump(mode="json") for v in document.extracted_values],
                document_metadata=dict(document.metadata),
```

- [ ] **Step 3c: Add the `metadata` param to `update_document`** (signature ~line 788 and body ~805):

```python
        extracted_values: list[ExtractedValue] | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Document:
        ...
            if extracted_values is not None:
                row.extracted_values = [v.model_dump(mode="json") for v in extracted_values]
            if metadata is not None:
                row.document_metadata = dict(metadata)
            row.updated_at = _now()
```

- [ ] **Step 3d: Hydrate in `_load_document`** (the `return Document(...)`, ~line 1282):

```python
            extracted_values=[ExtractedValue.model_validate(v) for v in row.extracted_values],
            metadata=dict(row.document_metadata or {}),
            folders=folders,
```

- [ ] **Step 3e: Write the migration** `migrations/versions/0005_add_document_metadata.py`:

```python
"""Add the documents.metadata free-form bag.

Revision ID: 0005_add_document_metadata
Revises: 0004_add_events
Create Date: 2026-06-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005_add_document_metadata"
down_revision: str | None = "0004_add_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("metadata", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("documents", "metadata")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_postgres_document_metadata.py -q` — Expected: PASS (3 tests).
Also run the existing store/pipeline tests to confirm no regression:
`uv run pytest tests/test_pipeline_stages.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/saga/storage/postgres.py migrations/versions/0005_add_document_metadata.py tests/test_postgres_document_metadata.py
git commit -m "feat: persist Document.metadata (column + migration 0005 + store CRUD)"
```

---

### Task 4: Export — emit metadata as top-level frontmatter

**Files:**
- Modify: `src/saga/export/okf.py:_frontmatter` (~line 54-95)
- Test: `tests/export/test_okf_render.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/export/test_okf_render.py`):

```python
def test_render_concept_emits_metadata_top_level_and_skips_reserved() -> None:
    doc = _doc(metadata={"project": "Apollo", "type": "SHOULD_NOT_OVERRIDE"})
    text = render_concept(doc, store_name="saga", public_base_url=None)
    fm = yaml.safe_load(text.partition("\n---\n")[0][len("---\n") :])
    assert fm["project"] == "Apollo"      # free-form key emitted top-level
    assert fm["type"] == "invoice"        # reserved key NOT overwritten by metadata
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/export/test_okf_render.py::test_render_concept_emits_metadata_top_level_and_skips_reserved -q`
Expected: FAIL — `KeyError: 'project'`.

- [ ] **Step 3: Emit metadata** at the end of `_frontmatter`, just before `return fm`:

```python
    # Free-form document metadata is emitted as top-level keys (visible to any OKF consumer),
    # but never allowed to shadow a reserved/saga_ key. See saga.okf_keys.
    for key, value in document.metadata.items():
        if not is_reserved_key(key) and key not in fm:
            fm[key] = value
    return fm
```

Add the import at the top of `src/saga/export/okf.py`:

```python
from saga.okf_keys import is_reserved_key
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/export/test_okf_render.py -q` — Expected: PASS (all, incl. new).

- [ ] **Step 5: Commit**

```bash
git add src/saga/export/okf.py tests/export/test_okf_render.py
git commit -m "feat(okf): emit Document.metadata as top-level frontmatter"
```

---

### Task 5: Import — capture metadata in both paths

**Files:**
- Modify: `src/saga/imports/okf.py` — `_import_concept` (Document ctor ~line 251) and
  `_reenrich_concept` (Document ctor ~line 349)
- Test: `tests/imports/test_concept_parse.py` (append) + `tests/imports/test_okf_importer.py`

- [ ] **Step 1: Write the failing test** (append to `tests/imports/test_concept_parse.py`; this
  file already imports `split_frontmatter` — add the metadata helper assertion):

```python
from saga.okf_keys import metadata_from_frontmatter


def test_metadata_helper_drops_reserved_and_saga_keys() -> None:
    fm = {"type": "invoice", "title": "X", "saga_id": "d1", "owner": "me", "rank": 2}
    assert metadata_from_frontmatter(fm) == {"owner": "me", "rank": "2"}
```

  (The end-to-end round-trip is Task 6; this task wires both Document constructors.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/imports/test_concept_parse.py -q` — Expected: PASS for the helper
(it exists from Task 1) — this confirms the contract. Then make the *wiring* change below and
rely on Task 6's round-trip test to prove both paths populate `metadata`.

- [ ] **Step 3a: `_import_concept`** — add the import at the top of `src/saga/imports/okf.py`:

```python
from saga.okf_keys import metadata_from_frontmatter
```

  and set `metadata` in the `Document(...)` ctor (~line 263, alongside `extracted_values`):

```python
            extracted_values=[ExtractedValue(**v) for v in fm.get("saga_extracted_values", [])],
            metadata=metadata_from_frontmatter(fm),
```

- [ ] **Step 3b: `_reenrich_concept`** — set `metadata` in its `Document(...)` ctor (~line 360,
  after `summary=`):

```python
            summary=fm.get("description"),
            metadata=metadata_from_frontmatter(fm),
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/imports/ -q` — Expected: PASS (no regressions; round-trip proven next).

- [ ] **Step 5: Commit**

```bash
git add src/saga/imports/okf.py tests/imports/test_concept_parse.py
git commit -m "feat(okf): capture free-form frontmatter into Document.metadata on import"
```

---

### Task 6: Lossless foreign round-trip test (headline)

**Files:**
- Test: `tests/imports/test_okf_roundtrip.py` (append a foreign-metadata case)

- [ ] **Step 1: Write the test.** Inspect the existing `test_okf_roundtrip.py` for its store/minio/
  queue fixtures and bundle-building helper, then add a test that: (a) writes a *foreign* concept
  (frontmatter with `type`, `title`, and extra keys `project: Apollo`, `priority: high`, no
  `saga_*`) into a bundle dir, (b) imports it via `OkfBundleImporter.run`, (c) asserts the stored
  document's `metadata == {"project": "Apollo", "priority": "high"}`, (d) exports via
  `OkfBundleBuilder`, and (e) asserts the re-exported concept's frontmatter still carries
  `project: Apollo` and `priority: high` at top level.

```python
async def test_foreign_metadata_survives_import_then_export(store, minio, queue) -> None:
    # ... build a foreign concept with extra top-level keys, import, fetch the doc ...
    doc = (await store.list_documents(page=1, page_size=10))[0][0]
    assert doc.metadata == {"project": "Apollo", "priority": "high"}
    # ... export via OkfBundleBuilder, read the concept back ...
    fm = yaml.safe_load(concept_text.partition("\n---\n")[0][len("---\n") :])
    assert fm["project"] == "Apollo" and fm["priority"] == "high"
```

  (Use the fixtures/helpers already present in the file; do not invent new infra.)

- [ ] **Step 2: Run** `uv run pytest tests/imports/test_okf_roundtrip.py -q` — Expected: PASS.
- [ ] **Step 3: Commit**

```bash
git add tests/imports/test_okf_roundtrip.py
git commit -m "test(okf): foreign metadata survives import->export round-trip"
```

---

### Task 7: OpenSearch mapping + projection

**Files:**
- Modify: `src/saga/storage/mappings.py` (`document_index_body` ~line 51; add a
  `build_metadata_text` builder near `build_value_terms` ~line 19)
- Modify: `src/saga/storage/opensearch.py:project_document` (source dict ~line 154-158)
- Test: `tests/test_mappings.py` (the file testing the builders) + a projection assertion

- [ ] **Step 1: Write the failing test** (append to the mappings test file):

```python
from saga.storage.mappings import build_metadata_text, document_index_body


def test_metadata_text_flattens_key_values() -> None:
    assert build_metadata_text({"project": "Apollo", "rank": "1"}) == "project: Apollo\nrank: 1"
    assert build_metadata_text({}) == ""


def test_document_index_body_has_metadata_fields(os_config) -> None:  # reuse the existing config fixture
    props = document_index_body(os_config)["mappings"]["properties"]
    assert props["metadata"]["type"] == "nested"
    assert props["metadata"]["properties"]["key"]["type"] == "keyword"
    assert props["metadata_text"]["type"] == "text"
```

  (If the test file builds the config inline rather than via a fixture, follow that pattern.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mappings.py -q` — Expected: FAIL — no `build_metadata_text`,
no `metadata` mapping.

- [ ] **Step 3a: Add the builder** in `mappings.py` (after `build_value_terms`):

```python
def build_metadata_text(metadata: dict[str, str]) -> str:
    """Flatten metadata to newline-joined ``key: value`` for the ``query_string`` keyword leg."""
    return "\n".join(f"{key}: {value}" for key, value in metadata.items())
```

- [ ] **Step 3b: Add mapping fields** to `document_index_body`'s `properties` (after
  `extracted_values`):

```python
                "metadata": {
                    "type": "nested",
                    "properties": {
                        "key": {"type": "keyword"},
                        "value": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 512}},
                        },
                    },
                },
                "metadata_text": {"type": "text"},
```

- [ ] **Step 3c: Project both forms** in `opensearch.py:project_document` (add to `source`, after
  `value_terms`):

```python
            "metadata": [{"key": k, "value": v} for k, v in document.metadata.items()],
            "metadata_text": build_metadata_text(document.metadata),
```

  Add `build_metadata_text` to the existing import from `saga.storage.mappings` at the top of
  `opensearch.py` (line ~31, beside `build_value_terms`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mappings.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saga/storage/mappings.py src/saga/storage/opensearch.py tests/test_mappings.py
git commit -m "feat(search): project Document.metadata (nested + metadata_text)"
```

---

### Task 8: Search builders — match + filter on metadata

**Files:**
- Modify: `src/saga/storage/mappings.py` — `build_document_filters` (~178), the `should`
  in `build_document_search_body` (~243)
- Test: `tests/test_mappings.py` (append)

- [ ] **Step 1: Write the failing test**:

```python
from saga.storage.mappings import build_document_filters, build_document_search_body


def test_build_document_filters_emits_nested_metadata_term() -> None:
    filters = build_document_filters(metadata={"project": "Apollo"})
    nested = [f for f in filters if "nested" in f and f["nested"]["path"] == "metadata"]
    assert nested, "expected a nested metadata filter"
    must = nested[0]["nested"]["query"]["bool"]["filter"]
    assert {"term": {"metadata.key": "project"}} in must
    assert {"term": {"metadata.value.keyword": "Apollo"}} in must


def test_document_search_body_matches_metadata_values() -> None:
    body = build_document_search_body(query="apollo", filters=[], from_=0, size=10)
    should = body["query"]["bool"]["must"][0]["bool"]["should"]
    assert any(
        c.get("nested", {}).get("path") == "metadata" for c in should
    ), "expected a nested metadata.value match clause"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_mappings.py -q` — Expected: FAIL.

- [ ] **Step 3a: Add the `metadata` param** to `build_document_filters` (signature + body, after
  the `extracted_values` loop):

```python
    extracted_values: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    ...
    for key, value in (metadata or {}).items():
        filters.append(
            {
                "nested": {
                    "path": "metadata",
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"metadata.key": key}},
                                {"term": {"metadata.value.keyword": value}},
                            ]
                        }
                    },
                }
            }
        )
    return filters
```

- [ ] **Step 3b: Match metadata values** in `build_document_search_body` — add to the `should`
  list (after the `extracted_values` nested clause):

```python
            {
                "nested": {
                    "path": "metadata",
                    "query": {"match": {"metadata.value": query}},
                }
            },
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_mappings.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saga/storage/mappings.py tests/test_mappings.py
git commit -m "feat(search): metadata filter clause + free-text match in document search"
```

---

### Task 9: Search service — thread the metadata filter + keyword field

**Files:**
- Modify: `src/saga/search/service.py` — default `keyword_fields` (~57) to include
  `metadata_text`; `hybrid_search` (~71) and `search_documents` (~216) gain a `metadata` param
  threaded into `build_document_filters(..., metadata=metadata)`
- Modify: `src/saga/core/config.py:124` — add `metadata_text` to `keyword_search_fields` default
- Test: `tests/` search-service test (follow the existing search-service test file)

- [ ] **Step 1: Write the failing test.** In the existing search-service test, assert that
  `SearchService(...)._keyword_fields` includes `"metadata_text"` and that calling
  `search_documents(..., metadata={"project": "Apollo"})` passes a nested metadata filter to the
  (faked) OpenSearch client. Mirror how the file already fakes `_opensearch` and inspects the
  `filters` argument.

```python
def test_default_keyword_fields_include_metadata_text() -> None:
    svc = SearchService(opensearch=..., embedder=...)  # use the file's existing construction
    assert "metadata_text" in svc._keyword_fields
```

- [ ] **Step 2: Run** — Expected: FAIL (`metadata_text` not in fields / no `metadata` param).

- [ ] **Step 3a:** default keyword fields in `service.py` (~57) → append `"metadata_text"`:

```python
        self._keyword_fields = keyword_fields or [
            "title^3",
            "summary^2",
            "content_markdown",
            "doc_type",
            "metadata_text",
        ]
```

- [ ] **Step 3b:** add `metadata: dict[str, str] | None = None` to both `hybrid_search` and
  `search_documents` signatures and pass `metadata=metadata` into each `build_document_filters(...)`
  call (the keyword-leg call ~108 and the `search_documents` call ~230). The semantic-leg
  `build_filters` (chunk index) is **not** changed — metadata is document-level.

- [ ] **Step 3c:** `config.py:124` default → add `"metadata_text"`:

```python
        default_factory=lambda: ["title^3", "summary^2", "content_markdown", "doc_type", "metadata_text"]
```

- [ ] **Step 4: Run** — Expected: PASS. Then `uv run pytest tests/ -k search -q` for no regressions.
- [ ] **Step 5: Commit**

```bash
git add src/saga/search/service.py src/saga/core/config.py tests/
git commit -m "feat(search): search + filter documents by metadata"
```

---

### Task 10: REST — PATCH/response/search schemas + validation

**Files:**
- Modify: `src/saga/api/schemas.py` — `DocumentResponse` (+`metadata`), `DocumentPatch`
  (+`metadata`), `SearchRequest` (+`metadata`), `DocumentSearchRequest` (+`metadata`)
- Modify: `src/saga/api/service.py:update_document` (~198) thread `metadata`; the search service
  calls (find where `search_documents`/`hybrid_search` are invoked) pass `metadata=request.metadata`
- Add validation: reject reserved/`saga_`/empty metadata keys → `ValidationError`
- Test: `tests/test_api_documents.py` (or the existing documents-route test file)

- [ ] **Step 1: Write the failing tests** (append to the documents-route test file; reuse its
  TestClient + auth fixtures):

```python
def test_patch_sets_metadata(client, auth_headers, a_document) -> None:
    r = client.patch(f"/documents/{a_document}", json={"metadata": {"project": "Apollo"}}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["metadata"] == {"project": "Apollo"}


def test_patch_rejects_reserved_metadata_key(client, auth_headers, a_document) -> None:
    r = client.patch(f"/documents/{a_document}", json={"metadata": {"saga_id": "x"}}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["code"] == "validation_error"
```

- [ ] **Step 2: Run** — Expected: FAIL (`metadata` unknown / not returned / not rejected).

- [ ] **Step 3a: Schemas** (`schemas.py`):
  - `DocumentResponse`: add `metadata: dict[str, str] = Field(default_factory=dict)`
    (after `extracted_values`). `from_document` uses `model_dump()`, so it flows automatically.
  - `DocumentPatch`: add `metadata: dict[str, str] | None = None`.
  - `SearchRequest` and `DocumentSearchRequest`: add
    `metadata: dict[str, str] = Field(default_factory=dict, description="Match metadata, e.g. {project: 'Apollo'}.")`.

- [ ] **Step 3b: Validation helper** — add to `src/saga/okf_keys.py`:

```python
def validate_metadata_keys(metadata: dict[str, str]) -> None:
    """Raise ValueError if any key is empty or SAGA-owned (reserved or ``saga_``)."""
    for key in metadata:
        if not key.strip():
            raise ValueError("Metadata keys must be non-empty.")
        if is_reserved_key(key):
            raise ValueError(
                f"Metadata key '{key}' is reserved (OKF-standard or 'saga_'-prefixed) "
                "and cannot be set; choose a different key."
            )
```

- [ ] **Step 3c: Service** (`api/service.py:update_document`): after building `fields`, validate and
  thread metadata:

```python
    if patch.metadata is not None:
        try:
            validate_metadata_keys(patch.metadata)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    document = await services.db.update_document(
        document_id,
        title=fields.get("title"),
        summary=fields.get("summary"),
        doc_type_id=fields.get("doc_type_id"),
        clear_doc_type="doc_type_id" in fields and fields.get("doc_type_id") is None,
        extracted_values=patch.extracted_values,
        metadata=patch.metadata,
    )
```

  Add imports: `from saga.okf_keys import validate_metadata_keys`. Ensure `ValidationError` maps to
  HTTP 400 (it already does per the error table).

- [ ] **Step 3d: Search routes/service calls** — wherever `search` and `search_documents` services
  are invoked from the route handlers (search the `routes/` for `hybrid_search(` /
  `search_documents(`), pass `metadata=request.metadata`.

- [ ] **Step 4: Run** — Expected: PASS. Then `uv run pytest tests/test_api_documents.py -q`.
- [ ] **Step 5: Commit**

```bash
git add src/saga/api/schemas.py src/saga/api/service.py src/saga/api/routes tests/
git commit -m "feat(api): edit + search documents by metadata (PATCH, /search, /documents/search)"
```

---

### Task 11: MCP — write tool + search-tool filter

**Files:**
- Modify: `src/saga/mcp/server.py` — `update_document_metadata` (~the tool def) add `metadata`
  param; `search_documents` + `hybrid_search` tools add a `metadata` filter param
- Modify: prompt docs under `prompts/mcp/` for the affected tools (if per-tool description files
  exist)
- Test: the MCP tool test files (e.g. `tests/test_mcp_*`)

- [ ] **Step 1: Write the failing test.** In the MCP update-tool test, call
  `update_document_metadata(document_id=..., metadata={"project": "Apollo"})` against the faked
  services and assert the document's metadata is updated; assert a reserved key returns an
  `{"error": ...}` dict (consistent with the tool's existing error-return style, e.g. the invalid
  category branch in `get_timeline`).

- [ ] **Step 2: Run** — Expected: FAIL (no `metadata` param).

- [ ] **Step 3a:** `update_document_metadata` — add the param and thread to the service:

```python
        metadata: Annotated[
            dict[str, str] | None,
            Field(description="Full replacement metadata map (string values)."),
        ] = None,
```

  Validate with `validate_metadata_keys` (catch `ValueError` → `return {"error": str(exc)}`) and
  pass `metadata=metadata` into the update path the tool already uses.

- [ ] **Step 3b:** `search_documents` + `hybrid_search` MCP tools — add
  `metadata: dict[str, str] | None = None` and pass it into the service call.

- [ ] **Step 3c:** update the matching `prompts/mcp/*.md` tool descriptions to mention the metadata
  filter / editable metadata, if those files exist for these tools.

- [ ] **Step 4: Run** — Expected: PASS. Then `uv run pytest tests/ -k mcp -q`.
- [ ] **Step 5: Commit**

```bash
git add src/saga/mcp/server.py prompts/mcp tests/
git commit -m "feat(mcp): edit + filter documents by metadata"
```

---

### Task 12: Requirement, CHANGELOG, roadmap

**Files:**
- Modify: `docs/requirements/01-functional-requirements.md` (add **FR-57**)
- Modify: `CHANGELOG.md` (Unreleased → Features)
- Modify: `docs/okf-integration-roadmap.md` (Deferred → mark the metadata item done)

- [ ] **Step 1:** Add FR-57 near the OKF requirements (FR-51…56). Suggested text:

```markdown
- **FR-57 — Document metadata.** Each document carries a free-form `metadata` string map
  (`dict[str,str]`), editable via `PATCH /documents/{id}` and the `update_document_metadata`
  MCP tool. Metadata keys must not be reserved OKF keys or `saga_`-prefixed. Metadata is
  projected to OpenSearch and is full-text searchable and exactly filterable
  (`/documents/search` and `/search` `metadata` map). On OKF export it is written as top-level
  frontmatter; on import any non-reserved frontmatter key is captured into it (lossless foreign
  round-trip).
```

- [ ] **Step 2:** CHANGELOG entry under `## [Unreleased]` → `### Features`:

```markdown
- **api/mcp/search**: documents carry a free-form, searchable `metadata` string map — edit via
  `PATCH /documents/{id}` and the `update_document_metadata` MCP tool; full-text + exact filter
  via `/documents/search` and `/search`. Round-trips through OKF as top-level frontmatter, so
  foreign bundles import and re-export their extra keys losslessly (FR-57).
```

- [ ] **Step 3:** In `docs/okf-integration-roadmap.md`, change the Deferred "Lossless foreign
  round-trip" bullet to `[x]` and note it shipped (Document.metadata bag).

- [ ] **Step 4: Commit**

```bash
git add docs/requirements/01-functional-requirements.md CHANGELOG.md docs/okf-integration-roadmap.md
git commit -m "docs: FR-57 document metadata; changelog + roadmap"
```

---

### Task 13: Final gates + Docker build

- [ ] **Step 1: Full gates**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expected: all green; coverage ≥ 80% on core packages. Fix any failure before proceeding.

- [ ] **Step 2: Docker build** (mandatory per workspace AGENTS.md — runtime code changed):

```bash
cd /d/Projekte/Archiv && docker compose build api worker
```

Expected: exit 0.

- [ ] **Step 3:** No commit unless a fix was needed.

---

## Notes for the executor

- **`metadata` shadowing in SQLAlchemy:** the ORM attribute is `document_metadata` mapped to the
  DB column name `"metadata"` (SQLAlchemy's declarative base reserves `.metadata`). This mirrors
  `folder_metadata`. Do not name the attribute `metadata`.
- **Pydantic `metadata`:** `Document.metadata` is fine on a Pydantic `BaseModel` (no clash).
- **Value coercion:** import coerces YAML scalars to `str`; REST/MCP enforce `dict[str,str]` at the
  schema boundary. Keep both.
- **Out of scope here:** the saga-ui metadata editor (its own spec/plan after this merges).
