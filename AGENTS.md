# AGENTS.md — guide for AI coding agents

This file explains how to work in the **DocStore** repository. Read it before making
changes. It complements [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Project in one paragraph

DocStore ingests documents, converts them to Markdown (Docling/Kreuzberg over HTTP),
enriches them with LLM-extracted metadata, indexes them in OpenSearch (a keyword
**document index** + a **vector index** of chunks), stores originals in MinIO, and
serves **hybrid search** to agents via an MCP server. Ingestion runs asynchronously
on an ARQ worker. See [`docs/requirements/`](docs/requirements/).

## Golden rules

1. **Python 3.12+, fully typed.** Every function/attribute is annotated; code must
   pass `uv run mypy` (strict). No untyped `Any` unless unavoidable and justified.
2. **Use `uv` for everything.** Add deps with `uv add <pkg>` (and `uv add --dev` for
   dev tools). Never hand-edit versions without updating `uv.lock`.
3. **English only** — code, comments, identifiers, docs, commits (NFR-5).
4. **Config over constants.** No hard-coded endpoints, models, limits, or secrets.
   Read from `config/*.yaml` (+ env). Secrets only via env/`.env`.
5. **Prompts live in `prompts/*.md`.** Never inline prompt text or MCP tool
   descriptions as Python string literals — load and render the Markdown files.
6. **Structured extraction uses `llm-structured-output`.** Whenever you extract
   structured data from text/documents with an LLM (e.g. metadata), use
   `extract_from_text`/`get_structured_data` against a LangChain chat model
   (`docstore.llm.providers.build_chat_model`) with a Pydantic schema. Do **not**
   hand-roll JSON parsing of LLM output — the library enforces the schema via
   tool-calling and retries on type/schema errors. Embeddings are separate.
6. **Actionable errors.** Raise from the `docstore.core.errors` hierarchy with
   messages saying what failed and how to fix it. No bare/silent failures.
7. **Structured logging.** Use `docstore.core.logging.get_logger("docstore.<area>")`;
   never `print`. Bind correlation/job ids where relevant.
8. **Tests with behaviour.** New/changed logic ships with unit tests (mock external
   services). Keep coverage ≥ 80% on core packages.
9. **Conventional Commits + Git Flow** (see below).

## Repository layout

```
src/docstore/
  core/         config, logging, errors, domain models
  api/          FastAPI app, auth, routes (Phase 2)
  mcp/          MCP server + tools (Phase 6)
  pipeline/     ARQ worker tasks: convert→analyse→chunk→embed→index
  converters/   Docling/Kreuzberg clients + routing
  llm/          LLM provider adapters + prompt loader
  embeddings/   embedding provider adapters
  storage/      OpenSearch + MinIO adapters
  chunking/     markdown + token splitter
  scripts/      backup/export
config/         YAML config (config, converters, providers, logging)
prompts/        LLM prompts + MCP tool descriptions (Markdown)
docs/           requirements, architecture, planning
tests/          unit tests (mirrors src layout)
docker/         Dockerfile
```

## Common commands

```bash
uv sync                       # install deps
uv run ruff check . --fix     # lint + autofix
uv run ruff format .          # format
uv run mypy                   # strict type check
uv run pytest                 # tests
docker compose up -d          # full stack
```

## Where things go

- New REST endpoint → `src/docstore/api/`, register on the app, add tests + update
  OpenAPI docs.
- New MCP tool → `src/docstore/mcp/`, description in `prompts/mcp/<tool>.md`.
- New converter/provider → implement the `Protocol` in the relevant package; wire it
  via config; do not couple call sites to a concrete implementation.
- New pipeline stage → add to `pipeline/tasks.py`, keep stages idempotent and update
  document status + actionable error handling.

## Conventions

- **Commits:** Conventional Commits — `feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `chore:`, `ci:`, `build:`, `perf:`. Reference requirement IDs (e.g.
  `FR-19`) where useful. Release notes are generated from commit history.
- **Branches (Git Flow):** `feature/*` and `fix/*` branch from and merge into
  `develop`; `release/*` and `hotfix/*` per Git Flow; `main` holds releases.
- **PRs:** small, focused, green CI (lint + type-check + tests), with docs updated.

## Definition of done

- Typed, linted, type-checked, tested, documented.
- No secrets committed; config-driven; errors actionable; logs structured.
- Requirement(s) referenced; plan/docs updated if behaviour changed.
