# Functional Requirements

> Status: Draft v1 · Project: **Saga** — a document store for RAG agents.

This document captures **what** the system must do. The "how" is described in
[`03-architecture.md`](03-architecture.md) and the rollout is described in
[`../planning/implementation-plan.md`](../planning/implementation-plan.md).

Requirements use stable IDs (`FR-x`) so they can be referenced from code, tests,
commit messages, and pull requests.

---

## 1. Vision & Scope

Saga is an open-source service that stores a **large number of documents** and
makes them usable by **RAG (Retrieval-Augmented Generation) agents**. It supports
**any document that can be converted to text**, including:

- native text documents (PDF, DOCX, PPTX, XLSX, HTML, Markdown, …),
- **scanned documents** that consist only of images (OCR),
- **images** from which text is extracted (OCR).

The system exposes a **REST API** for document management and backup, and an
**MCP (Model Context Protocol) server** that lets agents run **hybrid search**
(keyword + semantic, fused into a single ranked list) over the stored documents and
manage their organisation (folders, doc-types, notes).

**Storage architecture.** **Postgres** is the **system of record** for all document
metadata, folders, doc-types, notes, and document↔folder memberships. **OpenSearch**
holds a **rebuildable search projection** (full text + chunk vectors + denormalised
filter fields) and is *not* authoritative. **MinIO** stores the original binaries, and
**Redis + ARQ** drive asynchronous ingestion.

---

## 2. Actors

| Actor | Description |
|-------|-------------|
| **API client** | A human or service that manages documents via the REST API. |
| **RAG agent** | An LLM agent that queries documents via the MCP server. |
| **Operator** | Person who deploys/operates the stack (docker compose). |
| **Backup job** | A script or scheduled task that exports all documents. |

---

## 3. Document ingestion & lifecycle

- **FR-1 Upload** — A client can upload a document in any supported format through
  the REST API (`multipart/form-data`). The API accepts the binary plus optional
  client-supplied metadata.
- **FR-2 Format detection** — The system detects the document's type (by MIME type
  and/or extension and content sniffing) to choose the conversion service.
- **FR-3 Converter routing** — The mapping of *file type → conversion service*
  (Docling or Kreuzberg) is **configurable via YAML**. Default:
  **PDF → Docling**, **all other formats → Kreuzberg**.
- **FR-4 Conversion** — The selected service converts the document to **Markdown**
  text (with OCR for scanned/image documents). Conversion runs against the
  containerised Docling/Kreuzberg services over their HTTP APIs.
- **FR-5 LLM analysis** — After conversion, the Markdown is analysed by an LLM to
  extract metadata (see §4).
- **FR-6 Chunking** — The Markdown is split using a **Markdown-aware splitter**.
  Chunks that exceed the configured maximum are further split with a
  **token-based splitter**. The **maximum chunk size is configurable**.
- **FR-7 Embedding** — Each chunk is embedded into a vector using the configured
  embedding provider.
- **FR-8 Indexing** — The authoritative document record (text + metadata) is
  persisted in **Postgres**; a derived **document projection** (full text + summary +
  denormalised filter fields) is written to the OpenSearch **document index**, and
  chunks + vectors + snippets are written to the **vector index**, each referencing
  the parent document (see §6).
- **FR-9 Binary storage** — The original binary is stored in **MinIO** (object
  storage) and referenced from the document record.
- **FR-10 Delete** — A client can delete a document. Deletion removes the binary
  (MinIO), the document record, and **all** associated chunks/vectors.
- **FR-11 Update = replace** — Updating a file is implemented as **delete + re-create**
  (a new conversion/analysis/indexing cycle). The document id is preserved where
  possible (configurable: keep id vs. new id).
- **FR-12 Status tracking** — Because ingestion is asynchronous, each document has a
  processing status that is queryable via the REST API. The pipeline walks these in
  order: `pending` → `converting` → `classifying_type` → `analyzing` →
  `summarizing` → `classifying` → `indexing` → `ready` (or `failed`). Failures store
  an actionable error message.
- **FR-13 Idempotency / dedup** — Re-uploading the same binary is detected via a
  content hash; behaviour (reject / replace / allow duplicate) is configurable.

---

## 4. LLM metadata extraction

After conversion, every document is analysed by an LLM. All prompts live as
**Markdown files in a dedicated `prompts/` directory** (see NFR for traceability).

- **FR-14 Doc-type classification** — The document is assigned **exactly one
  doc-type** describing *what the document is* (e.g. invoice, contract,
  meeting_notes). A **doc-type** is a first-class entity (id, name, description of
  when to assign it, document count). During ingestion the LLM picks an existing
  doc-type or — when none fits and auto-create is enabled — coins a new one. Doc-types
  are deliberately distinct from folders, which describe *where* a document is
  organised (FR-16).
- **FR-15 Value extraction** — All relevant identifiers and numeric values are
  extracted and stored as **searchable metadata**, e.g. phone numbers, invoice
  numbers, contract numbers, customer numbers, IBANs, dates, amounts. Each value is
  stored with a typed key so search can filter on it.
- **FR-16 Folders & placement** — Documents are organised into **folders** (which
  replace the old derived "categories"). Folders are **hierarchical** (each has an
  optional `parent_id`), and carry a `name`, a `description` (LLM context for what
  belongs there), key/value `metadata`, timestamps, and a list of `notes`. A document
  may belong to **multiple folders** (n:m membership), while the physical binary is
  stored once. During ingestion the document is placed into 1..n folders by an LLM
  step that uses the folder tree, folder descriptions/metadata, the document summary,
  extracted values, and **similarity votes** aggregated from the most similar existing
  documents (semantic kNN over the summary embedding + lexical match + doc-type match
  + Jaccard over extracted values). The step may create new folders when none fit.
- **FR-17 Primary folder membership** — Exactly one of a document's folder
  memberships may be marked **`is_primary`**. The primary membership defines the
  canonical folder path used by the backup directory layout (§7); if none is marked
  primary, the first membership is treated as canonical.
- **FR-18 Structured, validated output** — LLM outputs are parsed into typed,
  validated models (via the `saidex` structured-output library, with retries on schema/type
  errors). Invalid/uncertain results are flagged rather than silently stored.
- **FR-38 Summary and title** — Each document gets a short, LLM-generated **`summary`**
  and a human-readable **`title`**. Both are produced in a single LLM call during the
  *summarize* pipeline stage, using the original **`filename`** as a hint (the LLM
  should coin a meaningful title rather than copying the filename verbatim). If the LLM
  returns an empty title or fails, `title` falls back to `filename`.
  The `filename` field stores the original upload name immutably; it is returned on
  document retrieval and in search results alongside `title`.
  The summary is embedded (`summary_embedding`, stored in Postgres) and used for
  document-level similarity at placement time and for keyword search.
- **FR-39 Notes** — Free-text **notes** (each with created/updated timestamps) can be
  attached to both **documents** and **folders** and managed over REST and MCP.

---

## 5. Search (REST + MCP)

- **FR-19 Hybrid search** — Search performs **fused hybrid search**: a keyword
  ranking (BM25 over the document projection — title, summary, content, doc-type) and
  a semantic ranking (kNN over chunk vectors) are merged into a **single ranked
  document list** using **Reciprocal Rank Fusion (RRF)** (constant `opensearch.rrf_k`,
  default 60). At least one of the keyword/semantic queries must be provided.
- **FR-20 Metadata filtering** — Search can be filtered by extracted metadata (§4),
  doc-type, `folder_id` (with `include_subtree` to include descendant folders), title,
  status, and a created-at date range.
- **FR-21 Snippet results** — Each fused result references the parent document (id,
  title, doc-type, summary, folder ids) with a relevance score and the best matching
  **snippet**.
- **FR-22 Folder browsing (MCP + REST)** — Both the REST API and MCP tools can:
  (a) return the **folder tree** (with subtree document counts), and (b) list **which
  documents live in a folder branch**.
- **FR-23 MCP tool catalogue** — Read tools: `hybrid_search`, `search_documents`,
  `get_document`, `get_folder_tree`, `get_folder`, `list_documents_in_folder`,
  `list_doc_types`. Write tools let agents reorganise the store:
  `update_document_metadata`, folder-membership tools
  (`assign_document_to_folder`, `remove_document_from_folder`, `set_document_folders`,
  `set_primary_folder`), folder CRUD (`create_folder`, `update_folder`,
  `delete_folder`), doc-type CRUD (`create_doc_type`, `update_doc_type`,
  `delete_doc_type`), and note CRUD for documents and folders.
- **FR-24 Performance** — The search path must be **performant** (low latency):
  pre-computed embeddings, pooled clients, denormalised filter fields, bounded result
  sizes (see NFR-Performance).

---

## 5b. Organisation management (REST + MCP)

- **FR-41 Folder management** — Folders support full CRUD over REST and MCP: create,
  read (single + tree), rename/move/describe/update metadata, and delete. Deletion
  takes a **strategy** — `reject` (refuse if the folder has children),
  `reparent` (attach children to the deleted folder's parent), or `cascade` (delete
  the subtree). Documents in a folder can be listed (with or without the subtree).
- **FR-42 Doc-type management** — Doc-types support CRUD over REST and MCP: create,
  list, read, update, and **delete only when unused** (otherwise rejected). Documents
  of a given doc-type can be listed (e.g. to reassign before deletion).
- **FR-43 Membership & editable fields** — A document's editable fields (`title`,
  `summary`, `doc_type_id`, `extracted_values`) can be patched. Folder membership is
  managed independently: list, replace the whole set, add one, set the primary, and
  remove one. Both REST and MCP expose these operations so agents and clients behave
  identically.

---

## 6. Storage model (Postgres + OpenSearch)

- **FR-40 Postgres system of record** — **Postgres** authoritatively stores
  documents (incl. `summary`, `summary_embedding`, doc-type reference, extracted
  values), `doc_types`, `folders`, document↔folder `memberships` (with the
  `is_primary` flag), and `notes` for documents and folders. All reads of a
  document/folder/doc-type are hydrated from Postgres.
- **FR-25 Search projection (two OpenSearch indices)** — OpenSearch holds a
  **rebuildable projection** derived from Postgres:
  - **Document index**: one record per document — full text + summary + denormalised
    filter fields (doc-type, `folder_ids`, `folder_ancestor_ids`, `primary_folder_id`,
    extracted values, status, timestamps) plus the `summary_embedding`. Optimised for
    **keyword search** and metadata filtering.
  - **Vector (chunk) index**: one record per chunk — the snippet text, the embedding
    vector, chunk position, denormalised filter fields, and a **reference
    (`document_id`)** to the document.
- **FR-26 Referential integrity** — Deleting a document removes its chunks and its
  projection; orphan chunks must not remain. Folder/doc-type integrity (memberships,
  document counts) is maintained in Postgres.
- **FR-27 Configurable vector params** — Embedding dimension, similarity metric, and
  ANN engine parameters are configurable and must match the active embedding model.

---

## 7. Backup & export

- **FR-28 Paginated export API** — The REST API can stream/page through **all**
  documents (cursor/`search_after` pagination) including text, metadata, and the
  binary reference.
- **FR-29 Backup script** — A provided script downloads **all** documents and writes
  them to a local **directory structure**.
- **FR-30 Directory layout** — The directory structure is built from each document's
  **`primary_folder_path`** (the list of folder names from the root down to the
  document's primary folder, supplied by the export endpoint). Documents that belong
  to no folder are placed under `_unfiled`.
- **FR-31 Per-document artefacts** — For each file, the backup stores: the original
  binary, the converted **Markdown** text, and **all metadata** (e.g. as a sidecar
  JSON file).

---

## 7b. Timeline & event log

- **FR-44 Tagged event store** — A single Postgres `events` table records timeline
  events with a `category` discriminator (`audit` | `content`), an `event_type`, a
  `document_id` and/or `folder_id`, `occurred_at` (real-world/event time) and
  `recorded_at` (archive time), an `actor`, a `summary`, and a `details` JSON blob.
  One `TimelineService` read path serves all consumers.
- **FR-45 Audit stream with rationale** — Lifecycle decisions are emitted as `audit`
  events: ingestion start, doc-type (re)classification, folder placement and moves,
  folder creation/rename. Automatic placement records **why** (the similar documents
  and folder votes that drove it), making self-organisation explainable. Emission is
  best-effort and never blocks ingestion.
- **FR-46 Content/timeline extraction** — An LLM step extracts real-world dates,
  appointments/deadlines, and recurring obligations from the document text
  (`kind` = past/future/recurring, with `date`, optional `end_date`, optional
  `recurrence` RRULE, `source_quote`, `confidence`). These persist as `content`
  events; they are a **derivable projection** — replaced (delete + reinsert) on
  re-analysis. A configurable confidence threshold filters low-quality hits.
- **FR-47 Recurrence on-read expansion** — A recurring rule is stored once (the RRULE
  in `details`), never materialised. Future occurrences are expanded **on read**
  within a configurable horizon, bounded by an `end_date` and a per-rule safety cap.
  Expanded occurrences are synthetic (not persisted) and back-reference the rule.
- **FR-48 Timeline read surface (REST + MCP)** — `GET /timeline` and
  `GET /documents/{id}/timeline` (filterable by `category`, type, folder-subtree, and
  time window) plus the MCP `get_timeline` tool expose the read path.
- **FR-49 Agenda / upcoming view** — `GET /agenda` and the MCP `get_agenda` tool return
  upcoming content events (appointments, deadlines, and expanded recurring
  occurrences) within the horizon, sorted ascending by real-world date. `GET /timeline`
  also accepts an `expand` flag.
- **FR-50 RRULE validation at extraction** — Recurrence patterns are validated as
  RFC 5545 RRULEs during extraction (a Pydantic field validator); invalid patterns are
  fed back to the LLM for self-correction (FR-18), so only valid rules are persisted.

---

## 7c. OKF interchange (export / import / round-trip)

- **FR-51 OKF export bundle** — `GET /export/okf` (plus a thin `saga-export-okf` client)
  streams the archive as an Open Knowledge Format `.tar.gz`: one concept file per
  document (YAML frontmatter + Markdown body + optional Notes), a per-folder reserved
  `index.md` (folder overview) and `log.md` (date-grouped, category-tagged change log
  fed by FR-44), and a root `index.md`. SAGA-specific metadata uses namespaced
  `saga_*` extension keys; `type` falls back to `document` when no doc-type is set.
- **FR-52 Originals option** — `--with-originals` colocates each document's original
  binary next to its concept file; the default is a pure-Markdown bundle.
- **FR-53 Machine-readable manifest** — The bundle also carries `saga-manifest.json`
  (folder tree + doc-type definitions with source ids) and `saga-events.jsonl` (every
  event verbatim). OKF consumers ignore these non-Markdown files; the SAGA import uses
  them for exact restore.
- **FR-54 OKF import — faithful restore** — `POST /import/okf` (plus a thin
  `saga-import-okf` client) restores a SAGA bundle into the same state: documents
  (preserving `document_id`, content, notes, doc-type, status), the folder tree and
  memberships, doc-types, and events (preserving `event_id`, folder ids remapped). It
  is idempotent (re-importing creates no duplicates) and re-indexes each document via a
  non-LLM `index_document` job so restored content events are not overwritten.
- **FR-55 Foreign-bundle re-enrich** — A bundle without `saga-manifest.json` (a foreign
  OKF bundle) is imported permissively: folders are rebuilt from the directory tree and
  each concept is re-enriched through the normal ingestion pipeline, seeded with its
  OKF `title`/`type`/`description`.
- **FR-56 Round-trip fidelity** — Exporting an archive and importing it into a fresh
  instance reproduces the same state (documents, metadata, content, notes, doc-type
  assignment, folder tree, memberships, and events), verified by a round-trip test.

---

## 8. Configuration

- **FR-32 YAML configuration** — Converter routing, chunking limits, LLM/embedding
  providers, OpenSearch/MinIO connection, logging, and feature toggles are
  configured via **YAML files** (with environment-variable overrides for secrets).
- **FR-33 Provider selection** — LLM and embedding providers are independently
  selectable among **Ollama**, **OpenAI**, and **Azure OpenAI**. Default: Ollama.
- **FR-34 Swagger UI toggle** — The REST API's **Swagger UI** can be enabled/disabled
  via configuration.

---

## 9. Security

- **FR-35 Bearer auth** — The REST API is protected by a **Bearer token**.
  Unauthenticated requests are rejected with a clear error.
- **FR-36 MCP auth** — The MCP HTTP endpoint is likewise protected (Bearer token).
- **FR-37 Secret handling** — Secrets (tokens, API keys, MinIO/OpenSearch creds) are
  supplied via environment variables / `.env`, never committed.

---

## 10. Supported formats (initial target)

PDF (incl. scanned), DOCX, DOC, PPTX, XLSX, CSV, HTML, Markdown, TXT, RTF, EML/MSG,
and images (PNG, JPG, TIFF, BMP, WEBP) via OCR. The authoritative list is derived
from the configured converter routing (FR-3).

---

## 11. Out of scope (v1)

- Multi-tenant isolation / per-user ACLs (single bearer token namespace).
- A web frontend/UI (only Swagger UI for the REST API).
- In-place editing of a document's **content** (the binary/Markdown is replaced via
  delete + re-create; only metadata, folders, and notes are editable in place).
- Real-time collaborative features.
