# CLAUDE.md

Guidance for Claude / AI coding agents working in **SAGA** (*Self-organizing Archive for
Generative Agents*) — the core backend. Read this before making any change. Keep changes
small, focused, typed, tested, and consistent with the conventions below.

## Project overview

SAGA ingests documents, converts them to Markdown (Docling/Kreuzberg over HTTP), enriches
them with LLM-extracted metadata, indexes them in OpenSearch (a keyword **document index**
+ a **vector index** of chunks), stores originals in MinIO, keeps Postgres as the system of
record, and serves **hybrid search** to agents via an MCP server and a REST API. Ingestion
runs asynchronously on an ARQ worker. See [`docs/requirements/`](docs/requirements/).

## Tech stack & requirements

- **Python 3.12+, fully typed** (`uv run mypy` strict must pass).
- **FastAPI** (REST) · **ARQ** (async worker) · **MCP** server.
- Infra: **PostgreSQL** · **MinIO** · **OpenSearch** · **Redis** · Docling/Kreuzberg · Ollama/OpenAI/Azure.
- **`uv`** for dependency & env management. Depends on the **`saidex`** structured-output library (PyPI).

## Repository layout

```
src/saga/
  core/         config, logging, errors, domain models
  api/          FastAPI app, auth, routes
  mcp/          MCP server + tools
  pipeline/     ARQ worker tasks: convert → analyse → chunk → embed → index
  converters/   Docling/Kreuzberg clients + routing
  llm/          LLM provider adapters + prompt loader
  embeddings/   embedding provider adapters
  storage/      OpenSearch + MinIO adapters
  chunking/     markdown + token splitter
config/         YAML config (config, converters, providers, logging)
prompts/        LLM prompts + MCP tool descriptions (Markdown)
docs/           requirements, architecture, planning
tests/          unit tests (mirror the src layout)
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

uv run saga-api               # REST API on :8000
uv run saga-worker            # ARQ worker
uv run saga-mcp               # MCP server on :8100
```

Run lint, type-check, and tests before considering a change done — they must be green.

## Key constraints (watch out for these)

- **Config over constants.** No hard-coded endpoints, models, limits, or secrets. Read from
  `config/*.yaml` (+ env); secrets only via env/`.env`.
- **Prompts live in `prompts/*.md`.** Never inline prompt text or MCP tool descriptions as
  Python string literals — load and render the Markdown files.
- **Structured extraction uses `saidex`.** For any LLM structured-data extraction, use
  `extract_from_text` / `get_structured_data` against a LangChain chat model
  (`saga.llm.providers.build_chat_model`) with a Pydantic schema. Do **not** hand-roll JSON
  parsing of LLM output. Embeddings are a separate path.
- **Actionable errors.** Raise from the `saga.core.errors` hierarchy with messages saying
  what failed and how to fix it. No bare/silent failures.
- **Structured logging.** Use `saga.core.logging.get_logger("saga.<area>")`; never `print`.
  Bind correlation/job ids where relevant.
- **Pluggability.** New converter/provider → implement the package `Protocol` and wire via
  config; don't couple call sites to a concrete implementation. Pipeline stages stay
  idempotent and update document status.
- **English only** — code, comments, identifiers, docs, commits.

## Where things go

- New REST endpoint → `src/saga/api/`, register on the app, add tests + update OpenAPI docs.
- New MCP tool → `src/saga/mcp/`, description in `prompts/mcp/<tool>.md`.
- New pipeline stage → `pipeline/tasks.py`, idempotent, with status + actionable errors.

## Coding conventions

- Every function/attribute annotated; avoid `Any` unless unavoidable and justified.
- New/changed logic ships with unit tests (mock external services); keep coverage ≥ 80% on
  core packages.
- Add deps with `uv add <pkg>` / `uv add --dev <pkg>`; never hand-edit versions without
  updating `uv.lock`. No secrets committed.

## Git workflow — branching, commits, PRs

**`main` and `develop` are protected**: pull requests are required, and only the repository
**admin/owner** may push directly (force-push and deletion are blocked). **Do not push
directly to `main`/`develop`** — always use a feature branch + PR.

- `main` — stable/release branch. `develop` — integration branch; feature work branches here.

```bash
git switch develop && git pull
git switch -c feature/<short-description>   # or fix/… , docs/… , chore/… , refactor/…
# …focused commits…
git push -u origin feature/<short-description>
gh pr create --base develop --fill          # PR targets develop
```

After review + green CI, **squash-merge** into `develop`. Promote to `main` via a
`develop → main` PR; tagging `vX.Y.Z` on `main` cuts a release.

**Commits**
- Only commit or push **when the human explicitly asks.** If you're on `main`/`develop`,
  branch first.
- **Conventional Commits** — `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`,
  `ci:`, `build:`, `perf:`. Reference requirement IDs (e.g. `FR-19`) where useful.
  Imperative mood, English. Release notes are generated from commit history.
- Never `--no-verify`, never force-push shared branches, never commit secrets or build artifacts.

## CI

- `.github/workflows/ci.yml` runs on push/PR to `main` and `develop`: lint + strict
  type-check + tests (must be green). Keep CI green; update the workflow in the same PR as
  the code it covers.

## Definition of done

Typed, linted, type-checked, tested, documented. No secrets committed; config-driven;
errors actionable; logs structured. Requirement(s) referenced; plan/docs updated if
behaviour changed.
