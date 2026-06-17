# OKF Faithful Round-Trip (Manifest + Import) — Design

- **Status:** Draft (approved in brainstorming, pending written-spec review)
- **Date:** 2026-06-17
- **Project:** `saga-core`
- **Builds on:** [OKF export v1](2026-06-17-okf-export-design.md) (PR #3), Phase 1 (event store)
  + Phase 2 (content events). Roadmap Track C/D.

---

## 1. Context & goal

SAGA can already **export** its archive as an OKF `.tar.gz` bundle. This spec adds the
**import** side and makes the pair a **faithful round-trip**: exporting an archive and
importing it into a fresh instance reproduces the same state — documents, metadata, content,
notes, doc-type assignment, the folder tree, folder memberships, **and** events (audit history +
content/timeline).

The hard requirement (from the user): *"an export must import back exactly the same."* That is
the headline acceptance criterion, encoded as a round-trip test (§9).

OKF concept files (frontmatter + markdown) do not carry all of this structural/historical state
(folder definitions, doc-type descriptions, the source ids needed to relink events, the events
themselves). So the bundle gains a small **machine-readable manifest** that OKF consumers ignore
and the SAGA import uses for exact restore.

### Two implementation phases (one design)
- **Phase A — export manifest:** extend `OkfBundleBuilder` to also emit `saga-manifest.json`
  (folders + doc-types) and `saga-events.jsonl` (all events).
- **Phase B — import:** `POST /import/okf`, an `OkfBundleImporter`, a `restore_events` store
  method, an `index_document` worker job, and a thin client.

### Decisions (from brainstorming)
- **Trust the frontmatter** for SAGA bundles (restore curated metadata, no LLM); **re-enrich**
  foreign OKF bundles (no `saga_*` keys) through the normal pipeline.
- Interface: **`POST /import/okf`** accepting an uploaded `.tar.gz` + a thin client (symmetric to
  export).
- Binary: **markdown-as-original fallback** (with-originals → restore the exact binary; else store
  the markdown body as the MinIO object).
- **Full round-trip incl. events**, via the machine-readable manifest + folder-id remapping.

---

## 2. Goals & Non-Goals

### Goals
- Phase A: emit `saga-manifest.json` + `saga-events.jsonl` from the export (OKF-ignored extras).
- Phase B: `POST /import/okf` restores a SAGA bundle faithfully (documents, folders, doc-types,
  memberships, events) and re-indexes; handles foreign OKF bundles by re-enriching.
- A round-trip test proving export→import reproduces the state.
- Idempotent re-import (no duplicates) via existing/added dedup keys.

### Non-Goals (v1)
- Lossless restore of **unknown** frontmatter keys from foreign bundles — i.e. keys that are
  neither `saga_*` nor OKF-standard (`type`/`title`/`description`, which §6 seeds into the
  re-enrich pipeline). `Document` has no free-form metadata bag, so unrecognised keys are dropped
  (future work).
- Streaming/partial import of very large bundles (v1 extracts to a temp dir).
- An import UI; merge strategies beyond `on_duplicate`; a full-store OpenSearch reindex (we index
  per imported document).

---

## 3. Architecture & flow (Phase B)

- **`POST /import/okf`** (behind `AuthDep`, in `src/saga/api/routes/`): accepts an uploaded
  `.tar.gz` (multipart file, like the document-upload route). Extracts it to a temp directory and
  hands the directory to the importer. Returns an **import summary** (§7).
- **`OkfBundleImporter`** (FastAPI-independent unit, mirrors `OkfBundleBuilder`): given the
  extracted bundle dir + `db`, `minio`, `queue`, `config`. Detects a SAGA bundle by the presence of
  `saga-manifest.json`:
  - **SAGA bundle → faithful restore** (§6): manifest-driven doc-types → folders → documents →
    events → index.
  - **Foreign bundle (no manifest) → re-enrich**: for each concept `.md` (a file *with*
    frontmatter; `index.md`/`log.md` are skipped), store the body/binary as a normal upload and
    enqueue `ingest_document` (the full LLM pipeline); reconstruct folders from the bundle
    directory tree. The OKF standard frontmatter is **seeded** into the new document (§6) so the
    pipeline starts from it rather than from nothing.
- **New worker job `index_document(ctx, document_id)`** (non-LLM): loads the document, embeds its
  summary, and runs the existing `index_chunks` stage (OpenSearch projection + chunk vectors). The
  faithful restore enqueues this (not `ingest_document`), so restored content events are **not**
  re-extracted/overwritten. Added to `WorkerSettings.functions`.
- **Thin client `saga-import-okf`** (`src/saga/scripts/`): tars a local bundle directory (or takes
  a `.tar.gz`) and uploads it to `POST /import/okf` with a Bearer token (mirrors
  `saga-export-okf`).

---

## 4. Machine-readable manifest (Phase A)

The export writes two machine-readable files at the bundle root, in addition to the OKF concept /
`index.md` / `log.md` files. OKF consumers ignore non-markdown files; the SAGA import uses these
as the exact-restore source.

### `saga-manifest.json`
```jsonc
{
  "version": "1",
  "store": "<store name>",
  "folders": [
    {"id": "...", "name": "Finanzen", "parent_id": null,
     "description": "Money", "emoji": "💰", "metadata": {}}
  ],
  "doc_types": [
    {"id": "...", "name": "invoice", "description": "A bill.", "emoji": "📄"}
  ]
}
```
- `folders[]` — from `PostgresStore.list_folders()`: source `id`, `name`, `parent_id`,
  `description`, `emoji`, `metadata`. Enables exact folder-tree restore + the source-id→new-id map.
- `doc_types[]` — from `list_doc_types()`: source `id`, `name`, `description`, `emoji`. Restores
  the doc-type description/emoji that the concept frontmatter lacks.

### `saga-events.jsonl`
One JSON object per line — `Event.model_dump(mode="json")` for every event (from `query_events`
paged with no filters): `event_id, category, event_type, document_id, folder_id, occurred_at,
recorded_at, actor, summary, confidence, dedupe_key, details`. JSONL keeps large event volumes
streamable.

**Documents stay the OKF concept files** (single source of truth; not duplicated in the manifest).
The manifest carries only what the concept frontmatter cannot fully hold: folder definitions,
doc-type definitions, events. A bundle without `saga-manifest.json` is still a valid OKF bundle.

---

## 5. Trust restore — concept → Document (SAGA bundles)

Per concept file (a `.md` with YAML frontmatter; the OKF-export contract):

| Document field | Source |
|---|---|
| `document_id` | `saga_id` (identity preserved → exact restore + idempotent re-import) |
| `title` / `filename` | `title` / `saga_filename` |
| `mime_type` / `size_bytes` / `content_hash` | `saga_mime_type` / `saga_size_bytes` / `saga_content_hash` |
| `doc_type` | `type` → `ensure_doc_type(name, …)` (see §6) |
| `summary` | `description` |
| `extracted_values` | `saga_extracted_values` |
| `notes` | `saga_notes` |
| `content_markdown` | body, with the exact Notes suffix stripped (below) |
| `created_at` / `updated_at` | `saga_created_at` / `timestamp` |
| `status` | `saga_status` (default `ready`) |

**`content_markdown` (deterministic):** when `saga_notes` is non-empty, the importer reconstructs
the **exact** Notes suffix the export appended — using the same shared render helper the export
uses — and strips it from the end of the body. This guarantees `content_markdown` round-trips
bit-for-bit (no fuzzy heading heuristic). The shared helper lives in `saga/export/` and is used by
both the builder (append) and the importer (strip).

**Binary:** if the bundle includes the original (`<title>__<id>.<ext>` next to the concept file),
restore it exactly: `minio.put_object(document_id, bytes, content_type=saga_mime_type)`. Otherwise
store the markdown body bytes as the MinIO object (`text/markdown`). Either way `minio_object` is
populated.

**Folder memberships:** from `saga_folders` (`[{id, name, primary}]`); each source folder `id` is
remapped to the new folder id via the §6 map; `primary` preserved.

---

## 6. Faithful restore order + id remapping (Phase B)

When `saga-manifest.json` is present, restore in this order:

1. **doc-types** — for each manifest entry, `ensure_doc_type(name, description, emoji)` (reuse by
   name, else create). Idempotent by name. Documents restore their doc-type by `type` (name).
2. **folders** — create topologically (parents before children) from the manifest:
   `create_folder(name, description, parent_id=<mapped new parent>, metadata, emoji)`, building a
   **source-folder-id → new-folder-id map**. Idempotent: if a folder with the same `parent+name`
   exists, reuse it (map the source id to the existing id).
3. **documents** — per concept file, the §5 trust-restore. Folder memberships from `saga_folders`
   are remapped via the id map (exact — no name/path matching needed); `primary` set.
4. **events** — from `saga-events.jsonl`, restore each **verbatim**: keep `document_id` (= the
   preserved `saga_id`), remap `folder_id` via the id map (`None` stays `None`), keep
   `event_id`/`occurred_at`/`recorded_at`/`actor`/`summary`/`confidence`/`details`. A new
   `PostgresStore.restore_events(events)` inserts verbatim and **skips events whose `event_id`
   already exists** (idempotent). Content events (`folder_id=None`) restore exactly with no
   re-extraction; audit events get the remapped `folder_id`.
5. **index** — enqueue `index_document` per restored document (embed summary + project +
   chunk/embed). Non-LLM, so restored content events are preserved.

The **source-folder-id → new-folder-id map** (built in step 2) is the linchpin, used in steps 3
and 4. `document_id` is preserved (`saga_id`), so documents need no remap.

**Foreign bundle (no manifest):** skip steps 1/2/4; reconstruct folders from the bundle directory
tree (each dir with an `index.md` is a folder; parent = containing dir); per concept, store the
body/binary as a normal upload (`create_document`) and enqueue `ingest_document` (full pipeline).
No event restore.

**Seeding the re-enrich pipeline from OKF frontmatter:** when a foreign concept carries the OKF
standard keys, they are used as the document's *initial* values (not as final, authoritative
metadata — the pipeline re-derives and may refine them):
- `title` → the new document's `title`/`filename`. The `summarize` stage may overwrite it with an
  LLM-derived title, falling back to this value when the LLM yields none.
- `type` → `ensure_doc_type(name)`, set as the document's initial `doc_type`. `ingest_document`
  passes it to `classify_doc_type` as `previous_doc_type` (a classification prior); the classifier
  may still reclassify.
- `description` → the document's initial `summary` (likewise refined by `summarize`).

This uses existing pipeline seams (`previous_doc_type`, the `filename` title-fallback) — no new
pipeline parameters. Unknown/non-standard keys are still dropped (§2 non-goal).

---

## 7. Robustness, response, dedup

- **Robustness:** per-concept and per-event errors are collected, not fatal — a malformed file or
  line is logged (`saga.import`) and skipped; the import continues and reports it. A failure on one
  document does not abort the whole import. Errors use the `saga.core.errors` hierarchy.
- **Import summary (response):** documents imported / skipped-duplicate / failed (with reasons);
  folders created / reused; doc-types created / reused; events restored / skipped; the error list.
- **Dedup / idempotency:** documents — the primary key is `saga_id` (the preserved
  `document_id`). On a `saga_id` collision, `config.dedup.on_duplicate` decides: `reject` skips,
  `replace` overwrites in place (same id), and `allow` **collapses to `reject`** — re-minting a new
  id would break round-trip identity, and the primary key cannot be duplicated. The
  `content_hash`/`allow` path applies only to the re-enrich (foreign-bundle) case, where ids are
  freshly minted. Events — by `event_id`; folders — by `parent+name`; doc-types — by `name`.
  Re-importing the same bundle into the same instance therefore creates no duplicates and keeps all
  ids identical (the folder/doc-type id map resolves to the existing rows).

---

## 8. Round-trip equality definition

Export(state) → import(fresh instance) reproduces:
- **Documents** — exact, incl. `document_id`, title/filename, mime/size/hash, summary,
  `extracted_values`, notes, `content_markdown`, doc-type assignment (by name), status.
- **Folder tree** — same structure (names, parent relationships, descriptions, emoji) and the same
  document memberships (primary + secondary).
- **doc-types** — same names + descriptions + emoji.
- **Events** — exact, incl. `event_id`, `document_id`, category/type, dates, actor, summary,
  confidence, details; `folder_id` points to the structurally-same folder.

**Identity note:** `document_id` and `event_id` are preserved exactly. Folder and doc-type **ids
are re-minted** on the fresh instance; their identity is structural (path/name) and kept consistent
via the id map. The round-trip test compares structurally (by path/name + the preserved ids), not
by raw folder/doc-type ids.

---

## 9. Testing

≥80% coverage on core packages; external services mocked where possible.
- **Phase A:** `OkfBundleBuilder` writes `saga-manifest.json` (folders + doc-types) and
  `saga-events.jsonl`; assert their contents against a seeded sqlite store (folders incl.
  description/emoji; events serialized verbatim).
- **Phase B units:** manifest parse; folder topological restore + id map (incl. reuse-by-name);
  document trust-restore mapping (incl. exact `## Notes`-suffix strip); event restore with
  `folder_id` remap + `event_id` idempotency; foreign-bundle routing (re-enrich) **incl. seeding
  `title`/`type`/`description` from OKF frontmatter into the new document before enqueue**; dedup
  paths.
- **Route test:** upload a `.tar.gz` (produced by the builder) to `POST /import/okf`; assert the
  documents/folders/events are restored.
- **Headline round-trip test:** seed a store (documents in folders, doc-types, audit + content
  events) → export to a bundle → import into a **fresh** sqlite store → assert structural equality
  (§8). This encodes the "exact round-trip" guarantee.
- `index_document` worker-job test (embeds summary + indexes; does not re-run LLM stages).
- Final gates: `ruff`, `mypy` strict, `pytest`, `docker compose build api worker` (exit 0).

---

## 10. Phasing

- **Phase A — export manifest** (extends `OkfBundleBuilder`, on the export branch / after PR #3):
  emit `saga-manifest.json` + `saga-events.jsonl`. Small, additive.
- **Phase B — import**: `POST /import/okf` route, `OkfBundleImporter`, the shared Notes-suffix
  render helper, `PostgresStore.restore_events`, the `index_document` worker job + wiring, and the
  `saga-import-okf` thin client.

Each phase is its own implementation plan; this design is the shared contract.

---

## 11. Open questions / future work

- **Foreign-bundle fidelity:** unknown frontmatter keys are dropped (no `Document` metadata bag).
  A future `Document.metadata` dict would enable lossless foreign round-trip.
- **Large bundles:** v1 extracts to a temp dir and reads events into memory per line; a streaming
  importer is future work.
- **Requirement IDs:** assign real FR/NFR IDs for OKF round-trip in `docs/requirements/`.
- **`index_document` reuse:** the new job is independently useful as a general "reindex one
  document" capability.
