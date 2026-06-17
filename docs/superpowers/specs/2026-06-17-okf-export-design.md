# OKF Export (v1) — Design

- **Status:** Draft (approved in brainstorming, pending written-spec review)
- **Date:** 2026-06-17
- **Project:** `saga-core` (generation) + a thin REST client
- **Related:** [OKF integration roadmap](../../okf-integration-roadmap.md) (Track B + Track D) ·
  [timeline event-log design](2026-06-16-timeline-event-log-design.md) (feeds `log.md`)

---

## 1. Context

This is the first concrete step that makes SAGA and the **Open Knowledge Format (OKF)** meet:
SAGA emits its archive as an **OKF bundle**. OKF is an interchange/serialization format —
this feature is a *projection* of the system of record (Postgres + MinIO + the event log),
never a change to how data is stored.

An OKF bundle is a directory of markdown files: one **concept file** per document (YAML
frontmatter + markdown body), plus the reserved per-directory files **`index.md`**
(progressive-disclosure overview) and **`log.md`** (chronological change log). The bundle is
"shippable as a tarball" and diffable in version control.

`log.md` is fed from the **timeline event log** (Phase 1 = audit events). Content/timeline
events (Track A Phase 2) will enrich `log.md` automatically later via the same query — no
export change required.

### Key decisions (from brainstorming)

- **Generation lives in saga-core, in-process**, exposed as a REST endpoint; a **thin client**
  triggers it and saves the bundle. (This revises the earlier "convert saga-backup's export"
  decision, because `log.md` needs the event log + `TimelineService`, which live in saga-core
  and cannot be imported by the direct-DB saga-backup tool.)
- **Delivery: a single `.tar.gz` stream** (matches OKF "tarball" + the existing `archive`
  pattern; one response carries the whole tree + optional binaries).
- **`log.md` is included in v1**, sourced from `TimelineService` (audit now, content later).
- **`resource`**: a configurable resolvable URL, with a `saga://` fallback.
- **Originals**: default pure markdown; `with_originals=true` colocates the binaries.

---

## 2. Goals & Non-Goals

### Goals
- A `GET /export/okf` endpoint that streams the whole archive as an OKF `.tar.gz` bundle.
- A reusable, FastAPI-independent `OkfBundleBuilder` that turns the stores + `TimelineService`
  into bundle files.
- A documented **frontmatter contract** (the format both export and the future import share).
- Per-folder `index.md` and `log.md`; concept files with frontmatter + body (+ `## Notes`).
- A thin REST client script to download/extract the bundle.
- Deterministic, git-diffable output.

### Non-Goals (explicitly out of v1)
- **OKF import** (roadmap Track C — separate spec).
- Content/timeline entries in `log.md` (arrive automatically once Track A Phase 2 lands).
- Subtree-scoped export (`folder_id` filter), cross-concept links, a SHA-256 manifest.
- Validation against Google's OKF visualizer / BigQuery Knowledge Catalog.
- Replacing the binary `archive`/`restore` backup — OKF export **complements** it.

---

## 3. Architecture & endpoint

### 3.1 `OkfBundleBuilder` (`src/saga/export/okf.py`)
A standalone unit (no FastAPI dependency) that, given its dependencies, yields the bundle as a
sequence of `(posix_path, bytes)` entries and writes them into a gzip-compressed tar.

Dependencies (narrow protocols, satisfied by the existing adapters):
- a document/folder reader (`PostgresStore`: `scroll_documents`, `list_folders`,
  `parents_map`, `folder_path`, doc-type lookups),
- a binary store (`MinioStore.get_object`) — only used when `with_originals=True`,
- a `TimelineService` for `log.md`,
- config: store name (`config.name`) and `export.public_base_url`.

The builder is responsible for: layout/paths, frontmatter rendering, body assembly,
`index.md` and `log.md` generation, and ordering/determinism. It does **not** know about HTTP.

### 3.2 Route `GET /export/okf` (`src/saga/api/routes/export.py`)
- Behind `AuthDep`, alongside the existing `/export/documents`.
- Query param: `with_originals: bool = False`.
- Builds the bundle into a `SpooledTemporaryFile` (tar.gz), then returns a `StreamingResponse`
  with `media_type="application/gzip"` and
  `Content-Disposition: attachment; filename="okf-<store>-<UTC-timestamp>.tar.gz"`.
- v1 exports the **whole** archive (subtree scoping is future).
- The route is thin: construct the builder from `services` and stream its output.

Memory: a `SpooledTemporaryFile` keeps small bundles in memory and spills large ones to disk,
so large archives (especially `with_originals`) do not exhaust memory.

### 3.3 Thin client (`src/saga/scripts/export_okf.py`)
Mirrors the existing REST-client pattern in `scripts/backup.py`: `GET /export/okf` with a
Bearer token, save the `.tar.gz` (optionally extract). Minimal CLI
(`--base-url`, `--token`, `--out`, `--with-originals`, `--extract`). Using `curl` directly is
also supported.

---

## 4. Bundle layout & frontmatter contract (Track D)

### 4.1 Layout
The tar contains a single root directory `okf-<store>-<timestamp>/`. The folder tree mirrors
each document's **primary** folder path; secondary memberships surface as `tags` and in the
`saga_folders` extension field. Layout helpers from `scripts/layout.py` are reused
(`sanitize_component`, `backup_relative_dir`, `backup_basename`, `original_filename`).

```
<bundle>/
  index.md                                  # root overview
  <Folder/Path>/index.md                    # folder note
  <Folder/Path>/log.md                      # folder change log (omitted if no events)
  <Folder/Path>/<title>__<doc_id>.md        # concept file (frontmatter + body)
  <Folder/Path>/<title>__<doc_id>.<ext>     # original binary (only with_originals=true)
  _unfiled/...                              # documents with no folder
```
The OKF **concept id** is the file path without `.md` (the OKF convention). Bundle-relative
links inside `index.md` are relative to the file's directory.

### 4.2 Concept-file frontmatter
OKF standard fields + `saga_`-namespaced extension keys (consumers preserve unknown keys, so
the round-trip is lossless). Rendered as a YAML block delimited by `---`.

| OKF field | SAGA source | Notes |
|---|---|---|
| `type` *(required)* | `Document.doc_type` | fallback `"document"` when null/empty |
| `title` | `Document.title` | |
| `description` | `Document.summary` | omitted if null |
| `resource` | `{public_base_url}/documents/{id}/file`, else `saga://{store}/documents/{id}` | |
| `tags` | names of **all** folder memberships (`folders[].name`), deduped + sorted | makes secondary placements visible |
| `timestamp` | `Document.updated_at` (ISO 8601) | |

Extension keys (all under `saga_`):
`saga_id` (document_id), `saga_doc_type_id`, `saga_content_hash`, `saga_mime_type`,
`saga_size_bytes`, `saga_status`, `saga_filename`, `saga_created_at`,
`saga_folders` (list of `{id, name, primary}` from `FolderRef`),
`saga_extracted_values` (list of `{key, type, value, normalized, confidence}`),
`saga_notes` (list of `{content, created_at, updated_at}`).

### 4.3 Body
The body is `Document.content_markdown` (empty string if null). If the document has notes, a
human-readable section is appended:
```markdown
<content_markdown>

## Notes

- <note.content>   (one bullet per note, in created_at order)
```
The same notes also appear structured in `saga_notes` (frontmatter) for lossless round-trip.

### 4.4 Cross-links
v1 emits **no** automatic document↔document links (SAGA has no explicit doc-to-doc links).
Bundle-relative "See also" links (e.g. to similar documents) are future work.

---

## 5. `index.md` and `log.md` formats

### 5.1 `index.md` (per folder + root)
OKF-conformant: **no frontmatter**. A heading plus grouped bullet lists, with descriptions
pulled from the linked entries. Built from the **direct** children only (not the subtree):

```markdown
# Finanzen / Rechnungen

## Subfolders
* [2026](2026/index.md) — <folder.description>

## Documents
* [Rechnung ACME 2026-01](Rechnung-ACME__a1b2.md) — <document.summary>
```
The root `index.md` lists top-level folders + `_unfiled`. The "— description" suffix is omitted
when there is no description/summary. An empty folder still gets an `index.md` (it may list
only subfolders or be a minimal heading).

### 5.2 `log.md` (per folder)
OKF-conformant: date-grouped, **newest first**, each entry tagged `[category] event_type` so a
reader can tell the audit view from the content view. Source:
`TimelineService.query(EventQuery(folder_id=<folder>, include_subtree=False, ...))`, paged via
`offset` until exhausted so the whole folder log is captured. The membership-resolved scoping
(timeline design §6.2) means document-level events for documents in this folder are included.

Grouping date per entry: `occurred_at` for `content` events, `recorded_at` for `audit` events.
Groups sorted descending; within a group, entries keep the query order (recorded_at desc).

```markdown
# Change log — Finanzen / Rechnungen

## 2026-06-13
* **[audit] placement** — Placed in 1 folder — similar to 'KFZ-Police 2025', 'Hausrat 2024'.

## 2026-05-01
* **[content] appointment** — Police-Ablauf 30.04.2027.
```
Each line uses the event's `summary`. A folder with **no** events gets **no** `log.md` (OKF:
reserved files only "when present"). The per-folder log is the folder's **own** scope (not the
subtree) to avoid duplicating child events upward; navigation is via the `index.md` hierarchy.

---

## 6. Config, error handling, determinism

### 6.1 Config (`config/config.yaml` + `AppConfig`, no hard-coding)
- `export.public_base_url: str | None` — base for `resource`; when unset, the `saga://` fallback
  is used. (A small `ExportConfig` block, mirroring sibling config models; v1 needs only this
  field.)

### 6.2 Error handling
- A missing binary in MinIO during `with_originals` export is **logged and skipped**, never
  fatal (consistent with the current export behaviour).
- Endpoint/`builder` errors use the `saga.core.errors` hierarchy; structured logging via
  `get_logger("saga.export")`.

### 6.3 Determinism (git-diffability)
- Folders ordered by their full path; documents in the stable order `scroll_documents`
  yields (by document id); frontmatter keys emitted in a fixed order; stable filenames
  (`<sanitized-title>__<doc_id>`). Re-exporting an unchanged archive yields a byte-identical
  tree (modulo the timestamped root dir name).

---

## 7. Testing

≥80% coverage on core packages; external services mocked.
- **Builder unit tests** (sqlite-backed `PostgresStore` + a fake `TimelineService`):
  frontmatter rendering (incl. `type` fallback, `resource` URL vs `saga://`, all `saga_*`
  keys, the `## Notes` section), `index.md` structure (subfolders + documents + omitted
  descriptions), `log.md` from canned events (date grouping + `[category]` markers + empty →
  omitted), path layout incl. `_unfiled`, determinism (stable ordering).
- **Route test** (`TestClient`, injected `Services`): `GET /export/okf` → open the returned
  `.tar.gz`, assert the root `index.md` exists, a concept `.md` parses to YAML frontmatter with
  a non-empty `type`, a `log.md` exists for a folder with events; then the `with_originals=true`
  variant includes the binary.
- Final gates: `ruff`, `mypy` strict, `pytest`, and the mandatory
  `docker compose build api worker` (exit 0).

---

## 8. Phasing / out-of-scope (recap)

v1 delivers: concept files + frontmatter contract + `index.md` + audit `log.md` + optional
originals + the `.tar.gz` endpoint + the thin client.

Deferred (separate work): content entries in `log.md` (auto via Track A Phase 2), subtree-scoped
export, cross-concept links, manifest/integrity, ecosystem validation
(visualizer/BigQuery), and the OKF **import** (Track C).

---

## 9. Open questions / future work

- **Requirement IDs:** assign real FR/NFR IDs in `docs/requirements/` for the OKF features.
- **`log.md` size:** very large folder logs are paged fully into the file; a future cap or
  windowing may be desirable.
- **`tags` source:** v1 uses folder-membership names; revisit whether value terms / doc-type
  should also contribute.
- **Round-trip contract:** finalize exactly which fields the future import restores losslessly
  (depends on adding a free-form metadata bag to `Document` — tracked in roadmap Track C).
