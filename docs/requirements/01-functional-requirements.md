# Functional Requirements

> Status: Draft v1 · Project: **DocStore** — a document store for RAG agents.

This document captures **what** the system must do. The "how" is described in
[`03-architecture.md`](03-architecture.md) and the rollout is described in
[`../planning/implementation-plan.md`](../planning/implementation-plan.md).

Requirements use stable IDs (`FR-x`) so they can be referenced from code, tests,
commit messages, and pull requests.

---

## 1. Vision & Scope

DocStore is an open-source service that stores a **large number of documents** and
makes them usable by **RAG (Retrieval-Augmented Generation) agents**. It supports
**any document that can be converted to text**, including:

- native text documents (PDF, DOCX, PPTX, XLSX, HTML, Markdown, …),
- **scanned documents** that consist only of images (OCR),
- **images** from which text is extracted (OCR).

The system exposes a **REST API** for document management and backup, and an
**MCP (Model Context Protocol) server** that lets agents run **hybrid search**
(keyword + semantic) over the stored documents.

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
- **FR-8 Indexing** — The document text + metadata are stored in the **document
  index**; chunks + vectors + snippets are stored in the **vector index**, each
  referencing the parent document (see §6).
- **FR-9 Binary storage** — The original binary is stored in **MinIO** (object
  storage) and referenced from the document record.
- **FR-10 Delete** — A client can delete a document. Deletion removes the binary
  (MinIO), the document record, and **all** associated chunks/vectors.
- **FR-11 Update = replace** — Updating a file is implemented as **delete + re-create**
  (a new conversion/analysis/indexing cycle). The document id is preserved where
  possible (configurable: keep id vs. new id).
- **FR-12 Status tracking** — Because ingestion is asynchronous, each document has a
  processing status (`pending`, `converting`, `analyzing`, `indexing`, `ready`,
  `failed`) that is queryable via the REST API. Failures store an actionable error
  message.
- **FR-13 Idempotency / dedup** — Re-uploading the same binary is detected via a
  content hash; behaviour (reject / replace / allow duplicate) is configurable.

---

## 4. LLM metadata extraction

After conversion, every document is analysed by an LLM. All prompts live as
**Markdown files in a dedicated `prompts/` directory** (see NFR for traceability).

- **FR-14 Classification** — The document is classified by **type** (e.g. invoice,
  contract, insurance policy, letter, ID document, …). The candidate label set is
  configurable.
- **FR-15 Value extraction** — All relevant identifiers and numeric values are
  extracted and stored as **searchable metadata**, e.g. phone numbers, invoice
  numbers, contract numbers, customer numbers, IBANs, dates, amounts. Each value is
  stored with a typed key so the MCP tool can filter on it.
- **FR-16 Hierarchical categorisation** — The document is categorised into a
  **hierarchical tree** (e.g. `Insurance/<insurance type>`). A single document MAY
  be placed in **multiple** branches of the tree.
- **FR-17 Folder structure field** — Each document stores a **`folder_structure`**
  field: an ordered list of hierarchical path entries. The **first entry** defines
  the canonical path used by the backup directory layout (§7).
- **FR-18 Structured, validated output** — LLM outputs are parsed into typed,
  validated models. Invalid/uncertain results are flagged rather than silently
  stored.

---

## 5. Search (REST + MCP)

- **FR-19 Hybrid search** — An MCP tool performs **hybrid search**: lexical
  (keyword/BM25) **and** semantic (vector) search combined and re-ranked into a
  single ranked result set.
- **FR-20 Metadata filtering** — Search can be filtered by extracted metadata
  (§4), document type, and category path.
- **FR-21 Snippet results** — Search returns matching **snippets** with a reference
  to the parent document (id, title, type, category path) and a relevance score.
- **FR-22 Tree browsing (MCP + REST)** — Both the REST API and MCP tools can:
  (a) return the **category tree** structure, and (b) list **which documents live
  in which branch**.
- **FR-23 MCP tool catalogue** — At minimum: `hybrid_search`, `get_category_tree`,
  `list_documents_in_category`, `get_document` / `get_document_metadata`.
- **FR-24 Performance** — The MCP search path must be **performant** (low latency):
  pre-computed embeddings, server-side hybrid pipeline, connection pooling,
  bounded result sizes (see NFR-Performance).

---

## 6. Storage model (OpenSearch)

- **FR-25 Two indices** —
  - **Document index**: one record per document — full text + metadata
    (type, extracted values, `folder_structure`, MinIO reference, hashes, status,
    timestamps). Optimised for **keyword search** and metadata filtering.
  - **Vector (chunk) index**: one record per chunk — the snippet text, the
    embedding vector, chunk position, and a **reference (`document_id`)** to the
    document index.
- **FR-26 Referential integrity** — Deleting a document removes its chunks; orphan
  chunks must not remain.
- **FR-27 Configurable vector params** — Embedding dimension, similarity metric, and
  ANN engine parameters are configurable and must match the active embedding model.

---

## 7. Backup & export

- **FR-28 Paginated export API** — The REST API can stream/page through **all**
  documents (cursor/`search_after` pagination) including text, metadata, and the
  binary reference.
- **FR-29 Backup script** — A provided script downloads **all** documents and writes
  them to a local **directory structure**.
- **FR-30 Directory layout** — The directory structure is built from the **first
  entry** of each document's `folder_structure` field.
- **FR-31 Per-document artefacts** — For each file, the backup stores: the original
  binary, the converted **Markdown** text, and **all metadata** (e.g. as a sidecar
  JSON file).

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
- Document editing/annotation in-place (only delete + re-create).
- Real-time collaborative features.
