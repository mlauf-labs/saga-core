# Changelog

All notable changes to this project are documented here. This project follows
[Conventional Commits](https://www.conventionalcommits.org/); release notes are
generated from the commit history (see `cliff.toml` and the release workflow).

## [Unreleased]

### Features
- **api**: `POST /documents/{id}/reanalyze` re-runs the full ingestion pipeline for an
  existing document (re-convert, regenerate metadata, re-chunk, re-index) — keeps the id
  and binary, resets status to `pending`, re-enqueues the job. The indexing stage now
  clears stale chunks before re-indexing, so re-runs are idempotent.
- **llm**: categorise documents into the **existing** folder structure (FR-16). The
  existing category paths are queried once, cached in memory (`CategoryCatalog`, TTL
  `llm.category_cache_ttl_seconds`) and passed to the categorisation step; the model
  reuses matching folders or creates new ones that fit the established conventions. The
  catalog is invalidated immediately on category-affecting DB writes (document create,
  metadata update, delete) so it stays current. New config:
  `llm.category_cache_ttl_seconds`, `llm.max_categories_in_prompt`.
- **api**: configurable CORS middleware for browser UIs (`API_CORS_ALLOW_ORIGINS` /
  `API_CORS_ALLOW_CREDENTIALS`; default allows all origins).
- **api/mcp**: document-level keyword search over title/content/type/category/values
  with filters — `POST /documents/search` and the `search_documents` MCP tool (FR-20).
- **search**: the semantic `/search` and `hybrid_search` now also match document
  titles and accept an exact-`title` filter (title denormalised onto chunks).
- **api/mcp**: edit document metadata — `PATCH /documents/{id}/metadata` and the
  `update_document_metadata` MCP tool — updating `doc_type`, `extracted_values`,
  `folder_structure` and `category_paths`, propagating changes to the search index.
- **api**: `GET /documents/{id}/file` accepts `disposition=inline|attachment` for
  in-browser preview (default `attachment`, unchanged).

- **llm**: extract document metadata (classification, identifier/value extraction,
  hierarchical categorisation) with the `llm-structured-output` library — LangChain
  tool-calling into Pydantic schemas with automatic **retry on schema/type errors**
  and an optional **fallback model** (FR-18). The bespoke JSON-parsing LLM adapters
  are replaced by a LangChain chat-model factory (Ollama/OpenAI/Azure); embeddings
  are unchanged. New config: `llm.fallback_model`, `llm.max_primary_retries`,
  `llm.max_fallback_retries`.

### Observability
- **llm**: each analysis step logs `analysis_step_start`/`done`/`failed` with
  `llm_calls`, `validation_retries`, `fallback_used` and `elapsed_ms`, and emits
  `llm_correction_sent` with the exact correction text sent back to the model on a
  retry — making slow/looping metadata extraction diagnosable (NFR-16).
- **llm**: disable the OpenAI client's built-in retries (`max_retries=0`) so request
  timeouts no longer multiply across the client and the structured-output retry layer.

### Bug Fixes
- **build**: copy `README.md`/`LICENSE` before `uv sync` so the image builds.
- **converters**: resolve a usable MIME type from the filename when uploads arrive
  as `application/octet-stream`, so conversion routing/extraction works.
- **llm**: tolerant parsing of analysis output (coerce numbers/objects, fall back
  `key`→`type`) and make each analysis step non-fatal so one malformed response no
  longer fails the whole document (FR-18).
- **llm**: cap generated tokens (`max_output_tokens`) and add a per-request timeout
  so a stuck/rambling model fails fast instead of hanging ingestion.
- **search**: denormalise extracted values onto chunks as `value_terms` and fix the
  metadata value filter so `filters={key: value}` actually matches (FR-20).

### Build & Ops
- **compose**: pass provider model/credential env vars through to containers; add an
  E2E override (`docker-compose.e2e.yml`) using `!reset`/`!override` for host ports.

## [1.0.0] - 2026-06-03

First public release: the complete ingestion-to-search pipeline for a RAG document
store.

### Features
- **Storage foundations**: OpenSearch document + kNN vector indices with a hybrid
  search pipeline, MinIO object storage, and an ARQ/Redis worker (FR-8/9/25/26).
- **REST API**: document management (upload, get, status, list, delete, replace =
  delete + re-create) with Bearer auth, actionable error handling, content-hash
  dedup, and a toggleable Swagger UI (FR-1/10/11/12/13/34).
- **Conversion**: Docling (PDF) and Kreuzberg (everything else, incl. OCR) HTTP
  clients with retries and timeouts, routed via `converters.yaml` (FR-3/4).
- **LLM analysis**: classification, identifier/number extraction, and hierarchical
  categorisation via pluggable Ollama/OpenAI/Azure providers, with prompts kept in
  Markdown files (FR-5/14/15/16/33).
- **Chunking, embeddings & indexing**: Markdown-aware splitting with token fallback,
  batched embeddings, and chunk/vector indexing referencing the document (FR-6/7/8).
- **Search**: hybrid (keyword + semantic) REST endpoints and category-tree browsing,
  plus an MCP server (Streamable HTTP, Bearer) exposing `hybrid_search`,
  `get_category_tree`, `list_documents_in_category`, and `get_document` (FR-19..24).
- **Backup & export**: cursor-paginated export API, binary download, and the
  `docstore-backup` script writing originals + Markdown + metadata into a directory
  tree derived from each document's folder structure (FR-28..31).

### Documentation
- Requirements, architecture, and phased implementation plan.
- Operations/deployment and configuration references; REST, MCP, and backup guides.
- `AGENTS.md` for AI coding agents; contributing guide and templates.

### Build & CI
- `uv`-managed project, multi-stage Dockerfile, and a full `docker compose` stack.
- GitHub Actions for lint (ruff), strict typing (mypy), tests (pytest, ≥80% coverage),
  image build, and changelog-based releases.

[1.0.0]: https://github.com/OWNER/docstore/releases/tag/v1.0.0
