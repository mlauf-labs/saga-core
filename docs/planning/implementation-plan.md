# Implementation Plan

> Status: Draft v1 · Project: **Saga**
>
> Rollout is organised into phases. Each phase is independently shippable, ends with
> tests + docs, and references the functional requirements (`FR-x`) it satisfies.
> Branching follows **Git Flow**; each phase is one or more `feature/*` branches
> merged into `develop`, with a `release/*` cut at milestone boundaries.

Legend: ☐ todo · ☑ done.

> **Post-1.0 architecture refactor.** Since the 1.0.0 release the storage model changed:
> **Postgres** is now the **system of record** (documents, doc-types, folders, notes,
> memberships) and **OpenSearch** is a **rebuildable search projection**. The old
> derived "categories" / `folder_structure` were replaced by first-class **folders**
> (hierarchical, n:m membership with a primary), **doc-types** became a first-class
> 1:1 entity, and documents gained a **summary** and **notes**. Ingestion is now
> convert → classify doc-type → extract → summarise (+embed) → compute similarity →
> place in folder(s) → persist → project + index. Hybrid search returns a **single
> fused list** via Reciprocal Rank Fusion. The historical phase records below are kept
> as-is; see [`../requirements/03-architecture.md`](../requirements/03-architecture.md)
> for the current design.

---

## Phase 0 — Project bootstrap *(this iteration)*

**Goal:** a clean, public-ready repository skeleton that builds and lints, with the
full toolchain wired up. No business logic yet.

- ☐ `pyproject.toml` (uv) with dependency groups (app, dev, test) and tooling config.
- ☐ Package skeleton `src/saga/**` with typed module stubs + public interfaces.
- ☐ Config files (`config/*.yaml`) + typed config loader (Pydantic Settings) with
      `${ENV}` resolution.
- ☐ Structured, coloured logging setup.
- ☐ `docker-compose.yml` with all services (API, MCP, worker, Docling, Kreuzberg,
      OpenSearch + Dashboards, MinIO, Redis, Ollama).
- ☐ App `Dockerfile` (multi-stage, uv).
- ☐ `prompts/**` markdown files (classification, extraction, categorisation, MCP tool
      descriptions).
- ☐ Repo meta: `README`, `AGENTS.md`, `CONTRIBUTING`, `CODE_OF_CONDUCT`, `LICENSE`,
      `.gitignore`, `.env.example`, issue/PR templates.
- ☐ GitHub Actions: CI (lint, type-check, test, build) + release (changelog).
- ☐ `pre-commit` config (ruff, type-check, conventional-commit lint).
- ☐ `git init`, Git Flow branches (`main`, `develop`), initial conventional commit.

**Satisfies:** scaffolding for NFR-1..5, NFR-6, NFR-24..32, FR-32..34 (config shape).

---

## Phase 1 — Storage foundations ✅

**Goal:** persistence layer works end to end (no conversion yet).

- ☑ OpenSearch client + index bootstrap/migrations for `documents` & `document_chunks`
      (kNN mapping, configurable dim) + hybrid **search pipeline**.
- ☑ MinIO client + bucket bootstrap; put/get/delete original binaries.
- ☑ Redis/ARQ wiring + worker entrypoint + health checks.
- ☑ Typed domain models (Document, Chunk, ExtractedValue, Status).
- ☑ Unit tests (mocked clients). Integration smoke test via compose: pending M2.

Delivered: `storage/opensearch.py`, `storage/minio.py`, `storage/mappings.py`,
`pipeline/queue.py`, `pipeline/worker.py`; hybrid query/pipeline builders; cascade
delete (FR-26); 95% coverage on implemented code.

**Satisfies:** FR-8, FR-9, FR-25, FR-26, FR-27; NFR-8, NFR-12, NFR-17.

---

## Phase 2 — REST API (document management) ✅

**Goal:** manage documents over HTTP with auth + Swagger.

- ☑ FastAPI app, Bearer auth dependency, error handlers (actionable messages).
- ☑ Endpoints: upload (202 + job), get, list (paginated), delete, status.
- ☑ Update = delete + re-create.
- ☑ Swagger UI toggle; OpenAPI metadata.
- ☑ Upload validation (size, empty), content-hash dedup (reject/replace/allow).
- ☑ API tests (TestClient) with injectable in-memory services.

Delivered: `api/app.py` (lifespan + DI), `api/dependencies.py` (Services container +
auth, store Protocols), `api/errors.py` (SagaError → HTTP), `api/schemas.py`,
`api/service.py` (ingest/dedup/replace logic), `api/routes/documents.py`. See
[`../api/rest-api.md`](../api/rest-api.md). Coverage 94%.

**Satisfies:** FR-1, FR-2, FR-10, FR-11, FR-12, FR-13, FR-28; NFR-13, NFR-18..20, NFR-29.

---

## Phase 3 — Conversion services integration ✅

**Goal:** convert any supported format to Markdown via the containerised services.

- ☑ Docling + Kreuzberg HTTP clients (typed, tenacity retries, timeouts).
- ☑ Converter registry driven by `converters.yaml` (PDF→Docling, else→Kreuzberg),
      with startup validation of routing targets.
- ☑ OCR config (enabled + languages) surfaced via YAML per service.
- ☑ Worker stage: download binary → convert → store Markdown; status transitions
      (converting → ready / failed with actionable error).
- ☑ Unit tests for routing, client adapters (respx-mocked HTTP), and the worker
      stage. Compose integration test: pending M2.

Delivered: `converters/{config,base,docling,kreuzberg,registry}.py`,
`pipeline/stages.py`, `pipeline/tasks.py` (orchestration), `OpenSearchStore.update_content`.
Coverage 94%.

**Satisfies:** FR-3, FR-4; NFR-7, NFR-14, NFR-15.

---

## Phase 4 — LLM analysis & metadata extraction ✅

**Goal:** classification, value extraction, hierarchical categorisation.

- ☑ LLM provider abstraction + Ollama/OpenAI/Azure adapters via config (FR-33).
      (Embedding adapters follow in Phase 5.)
- ☑ Prompt loader/renderer reading `prompts/analysis/*.md` (NFR-30).
- ☑ Structured outputs → validated Pydantic models; robust JSON extraction;
      per-field confidence; malformed responses raise actionable `AnalysisError`.
- ☑ Worker stage: classify → extract values → categorise → write metadata +
      folder_structure; status `analyzing` between conversion and ready.
- ☑ Unit tests with scripted/mocked LLM responses + mocked SDK clients.

Delivered: `llm/{config,providers,schemas,analyzer}.py`, `pipeline/stages.analyze_metadata`,
`OpenSearchStore.update_metadata`; providers wired into the worker. Coverage 94%.

**Satisfies:** FR-5, FR-14..18, FR-33; NFR-30, NFR-34.

---

## Phase 5 — Chunking & embeddings & indexing ✅

**Goal:** complete the ingestion pipeline into the vector index.

- ☑ Markdown-header splitter + token-splitter fallback; configurable max size/overlap;
      injectable token length function (FR-6).
- ☑ Embedding provider abstraction + Ollama/OpenAI/Azure adapters with batching (FR-7).
- ☑ Build + bulk-index chunk records referencing the document; referential integrity
      on delete already enforced (Phase 1, FR-8/26).
- ☑ Unit tests (chunker, embeddings mocked, indexing stage). End-to-end compose test:
      pending M3.

Delivered: `chunking/__init__.py` (MarkdownChunker), `embeddings/{base,config,providers}.py`,
`pipeline/stages.index_chunks`; chunker+embedder wired into the worker; ingest now runs
`converting → analyzing → indexing → ready`; embedding/index dimension-mismatch warning.
Coverage 94%.

**Satisfies:** FR-6, FR-7, FR-8; NFR-10..12.

---

## Phase 6 — Search (REST + MCP) ✅

**Goal:** performant hybrid search + tree browsing for agents.

- ☑ Hybrid query builder (BM25 + kNN + filters) using the search pipeline (Phase 1).
- ☑ REST endpoints: `POST /search`, `GET /categories/tree`,
      `GET /categories/{path}/documents`.
- ☑ MCP server (FastMCP, Streamable HTTP, Bearer middleware) with tools
      `hybrid_search`, `get_category_tree`, `list_documents_in_category`, `get_document`.
- ☑ MCP tool descriptions loaded from `prompts/mcp/*.md` (NFR-30).
- ☑ Performance: warm pooled clients, bounded `top_k`, denormalised chunk filters,
      stateless HTTP, derived category tree via a single aggregation.

Delivered: `search/{tree,service}.py`, `api/routes/search.py`, `mcp/{server,auth}.py`,
`mcp/__main__.py`, `OpenSearchStore.category_terms`/`list_documents_in_category`;
`SearchService` shared by REST + MCP. Coverage 94%.

**Satisfies:** FR-19..24; NFR-10, NFR-12.

---

## Phase 7 — Backup & export ✅

**Goal:** export everything to a directory tree.

- ☑ Paginated export endpoint `GET /export/documents` (`search_after` cursor,
      opaque base64 token) including content + metadata.
- ☑ Binary download endpoint `GET /documents/{id}/file`.
- ☑ `scripts/backup.py` (`saga-backup`): pages the export API and writes, per
      document, into a directory from `folder_structure[0]`: the original binary, the
      `.md` text, and a `*.metadata.json` sidecar.
- ☑ Tests for cursor codec, scroll store, export route, layout helpers, and the
      backup runner (respx-mocked).

Delivered: `OpenSearchStore.scroll_documents`, `api/cursor.py`, `api/routes/export.py`,
`scripts/layout.py`, `scripts/backup.py`. Coverage 94%.

**Satisfies:** FR-28..31.

---

## Phase 8 — Hardening, docs & release ✅

**Goal:** production-readiness + first public release.

- ☑ Comprehensive interface docs (REST + MCP + backup), configuration reference,
      operations/deployment guide, and a docs index.
- ☑ Coverage gate (≥80%), security review (prod OpenSearch security + TLS, secrets,
      least privilege), error-message + log-structure review.
- ☑ API healthcheck + startup ordering; image-pinning guidance + resource sizing.
- ☑ `release/1.0.0` → `CHANGELOG.md` (Conventional Commits) → tag `v1.0.0` on `main`.

**Satisfies:** NFR-22..28, NFR-31, NFR-35; FR overall.

---

## Cross-cutting (every phase)

- Conventional Commits; PRs target `develop`; CI green before merge.
- New/changed behaviour ships with unit tests and doc updates.
- Public interfaces are fully typed and pass strict type checking.
- Errors are actionable; logs are structured + coloured.

---

## Milestones

| Milestone | Phases | Outcome |
|-----------|--------|---------|
| **M1 – Skeleton** | 0 | Repo builds, lints, compose boots infra. |
| **M2 – Ingest** | 1–3 | Upload → stored binary + Markdown. |
| **M3 – Enrich** | 4–5 | Documents fully analysed, chunked, embedded, indexed. |
| **M4 – Query** | 6 | Agents search via MCP; tree browsing. |
| **M5 – 1.0.0** | 7–8 | Backup, docs, release. |
