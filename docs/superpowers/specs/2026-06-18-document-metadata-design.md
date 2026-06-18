# Document Metadata Bag — Design

- **Status:** Approved in brainstorming (pending written-spec review)
- **Date:** 2026-06-18
- **Project:** `saga-core` (capability) + `saga-ui` (editor surface)
- **Requirement:** **FR-57** (free-form, searchable document metadata) — see
  `docs/requirements/01-functional-requirements.md`
- **Related:** [OKF faithful round-trip](2026-06-17-okf-faithful-round-trip-design.md) §11
  (the gap this closes) · [OKF integration roadmap](../../okf-integration-roadmap.md)
  (Deferred items → "Lossless foreign round-trip")

---

## 1. Context & goal

`Document` has no free-form metadata field (folders already have `Folder.metadata`). Two
consequences:

1. **Foreign OKF bundles do not round-trip losslessly.** On import of a *foreign* bundle
   (concept files with no `saga_*` keys), any frontmatter key SAGA does not recognise is
   dropped, so a re-export cannot reproduce it (round-trip spec §11 open question).
2. **Users/agents cannot attach arbitrary key/values** to a document.

This feature adds a **free-form, user- and agent-editable, searchable** `metadata` bag to the
document. It is a normal record field — Postgres stays the system of record; OpenSearch gets a
projection so metadata is searchable and filterable.

## 2. Decisions (locked in brainstorming)

- **Value typing:** `metadata` is `dict[str, str]` — a flat key→string map. Numbers/dates are
  stored as their string form. (Simplest to edit, validate, project, and round-trip.)
- **OKF emission:** metadata keys are emitted as **top-level** frontmatter keys, visible to any
  OKF consumer. On import, every frontmatter key that is **not reserved** and **not `saga_*`**
  is captured into `metadata`. This is true lossless foreign round-trip.
- **Searchable + filterable** via OpenSearch (REST + MCP). Document-level only.
- **Editable** via REST `PATCH`, the MCP write tool, and the saga-ui document detail.

## 3. The round-trip contract

### Reserved keys
The frontmatter keys SAGA itself owns (emitted by `export/okf.py:_frontmatter`) are
**reserved** and never live in `metadata`:

```
RESERVED = {"type", "title", "description", "tags", "resource", "timestamp"}
            ∪ every key starting with "saga_"
```

(`description` ↔ `summary`, `tags` ↔ folder names, `timestamp` ↔ `updated_at`; the `saga_*`
keys carry the faithful-round-trip fields.)

### Export (`src/saga/export/okf.py:_frontmatter`)
After building the reserved + `saga_*` block, append each `metadata` item as a top-level
frontmatter key **only if** its key is not in `RESERVED` and does not start with `saga_`. (By
§5 validation such keys can never be stored, so this is a defensive guard, not a data path.)

### Import (`src/saga/imports/okf.py`)
Both import paths (SAGA bundle with manifest; foreign bundle re-enrich) compute:

```python
metadata = {
    k: str(v)
    for k, v in fm.items()
    if k not in RESERVED and not k.startswith("saga_")
}
```

and set it on the constructed `Document`. SAGA bundles thereby preserve any extra non-`saga_`
keys; foreign bundles preserve their unknown top-level keys verbatim. Values are coerced to
`str` (frontmatter YAML may parse a bare number/bool) so the `dict[str, str]` contract holds.

## 4. Data model & storage

- **Model** (`src/saga/core/models.py`): add
  `metadata: dict[str, str] = Field(default_factory=dict)` to `Document` (mirrors
  `Folder.metadata`).
- **Postgres**: add a JSON column `documents.metadata` (NOT NULL, default `{}`), following the
  exact pattern of the existing `folders.metadata` column. The store methods that write/read a
  document (`create_document`, `update_document`, `get_document`, and the export
  `scroll_documents` path) persist and hydrate it.
- **Migration**: `migrations/versions/0005_add_document_metadata.py` (head is
  `0004_add_events`). `upgrade` adds the column with server default `'{}'`; `downgrade` drops
  it.

## 5. Validation

Enforced at every write boundary (REST, MCP, and import coercion):

- **Keys**: non-empty after strip; **must not** be in `RESERVED` and **must not** start with
  `saga_`. A violating key on REST/MCP → `400 validation_error` with an actionable message.
  (Import is lenient by construction — it only ever *collects* non-reserved/non-`saga_` keys,
  so it cannot produce an invalid key.)
- **Values**: must be strings (enforced by the `dict[str, str]` type; a non-string value on
  REST → 422 from the schema).

## 6. OpenSearch projection (searchable + filterable)

`metadata` is **document-level**, so it is projected only into the **document index**
(`storage/mappings.py:document_index_body`); the chunk/vector index is unchanged (the semantic
kNN leg is chunk text). Mirrors the existing `extracted_values` precedent — a nested field for
exact filtering + a flattened text field for free-text.

### Mapping additions (`document_index_body`)
- `metadata`: **nested** `{ key: keyword, value: { type: text, fields.keyword: keyword } }`
  → exact `key=value` filtering and full-text matching on values.
- `metadata_text`: **text** — the metadata projected as newline-joined `"key: value"` lines
  → lets the hybrid **keyword leg** (`query_string`, which cannot target nested fields) match
  metadata.

The projection writer (`storage/opensearch.py:project_document` and the pure builder it uses)
derives both forms from `document.metadata`. New fields are **additive** to the mapping (no
breaking reindex); documents back-fill as they are re-projected. The mapping is also applied to
existing indices via a `put_mapping` on bootstrap/ensure-index (additive field add).

### Search wiring
- **`POST /documents/search`** (`build_document_search_body`): add a nested `match` on
  `metadata.value` to the `should` clauses, so free-text document search matches metadata
  values.
- **`POST /search`** keyword leg (`build_keyword_query_body`): the search service adds
  `metadata_text` to the searched `fields`, so hybrid search matches metadata too.
- **Filtering**: a **new `metadata: dict[str, str]` map** on the `/documents/search` and
  `/search` request bodies (and the corresponding MCP tools), kept **separate** from the
  existing extracted-values `filters` map so a metadata key cannot be confused with an
  extracted-value key. `build_document_filters` gains a `metadata` param emitting a nested term
  filter (`metadata.key` + `metadata.value.keyword`), mirroring `extracted_values`.

### Re-projection on edit
The `PATCH`/edit path already re-projects the document to the search index ("changes propagate
to the search projection so filters stay consistent"), so editing `metadata` updates OpenSearch
immediately. `reanalyze` likewise re-projects.

## 7. Editing surfaces

### REST (`src/saga/api/routes/documents.py` + schemas)
- `PATCH /documents/{id}`: add optional `metadata: dict[str, str] | None`. Provided ⇒
  **replaces** the whole dict (consistent with the existing `title`/`summary`/`doc_type_id`/
  `extracted_values` replace semantics); omitted ⇒ unchanged. Validated per §5.
- `metadata` is included in `DocumentResponse` (so `GET /documents/{id}` and list/search
  results carry it).

### MCP (`src/saga/mcp/server.py:update_document_metadata`)
- Add a `metadata: dict[str, str] | None` param (same replace semantics + validation).
- The search tools (`search_documents`, `hybrid_search`) gain an optional `metadata` filter
  map.

### UI (saga-ui — separate deliverable)
- Document detail gains a **Metadata** section: editable key/value rows, saved via the existing
  API client (`PATCH /documents/{id}`). Read-only display when empty offers an "Add metadata"
  affordance. (Detailed in the saga-ui plan; not in the saga-core plan.)

## 8. Non-goals (v1)

- **The .NET importer** (`saga-importer`) is unchanged — it uploads originals; it does not set
  metadata.
- **No metadata schema / typed values** — strings only, no per-key type system.
- **No dedicated metadata search endpoint** — metadata rides the existing
  `/documents/search` + `/search` (free-text + the new filter map).

## 9. Scope split — two deliverables

Per the writing-plans scope check, this is two independently-shippable pieces:

1. **saga-core** (this spec's substance): model + migration + round-trip (export/import) +
   OpenSearch projection/search/filter + REST + MCP + tests. **Implement first.**
2. **saga-ui**: the metadata editor in document detail, consuming the new API. **Its own
   spec/plan after saga-core merges.**

## 10. Testing strategy (saga-core)

- **Model/store/migration**: `create_document` with metadata → `get_document` returns it;
  `update_document(metadata=...)` replaces; default is `{}`.
- **Round-trip (the headline)**: a foreign-style concept with extra top-level keys → import →
  `metadata` populated (reserved/`saga_` excluded) → export → the same top-level keys reappear
  identically. Extend `tests/export/test_okf_conformance.py` / the import round-trip tests.
- **Export** (`test_okf_render.py`): metadata keys emitted top-level; a key equal to a reserved
  name or `saga_*` is not emitted.
- **Import** (`tests/imports/`): leftover frontmatter keys captured; reserved/`saga_` keys not;
  bare numeric/bool YAML values coerced to strings.
- **OpenSearch builders** (`tests/` for `mappings.py`): `metadata` nested + `metadata_text`
  present in the mapping; `build_document_search_body` matches `metadata.value`;
  `build_document_filters(metadata=...)` emits the nested term filter; `build_keyword_query_body`
  includes `metadata_text`.
- **REST**: `PATCH` replace + reserved-key rejection (400); `metadata` in `DocumentResponse`;
  `/documents/search?metadata={k:v}` filters.
- **MCP**: `update_document_metadata(metadata=...)` and the search-tool `metadata` filter.

## 11. Open questions / future work

- **Per-key search boosting / typed values** — deferred (strings only for v1).
- **saga-ui editor UX** (bulk edit, validation messaging) — its own brainstorm.
