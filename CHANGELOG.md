# Changelog

All notable changes to this project are documented here. This project follows
[Conventional Commits](https://www.conventionalcommits.org/); release notes are
generated from the commit history (see `cliff.toml` and the release workflow).

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
