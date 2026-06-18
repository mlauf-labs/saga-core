# SAGA

> **SAGA** — *Self-organizing Archive for Generative Agents* — is a self-organizing,
> AI-native document archive for RAG agents: ingest any text-convertible document, enrich it
> with LLM-extracted metadata, keep it in **Postgres** (system of record), project it
> into **OpenSearch** (keyword + vector), and let agents query it via fused **hybrid
> search** and organise it over MCP.

[![CI](https://github.com/mlauf-labs/saga-core/actions/workflows/ci.yml/badge.svg)](https://github.com/mlauf-labs/saga-core/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> **Status:** 1.0.0 — the full ingestion-to-search pipeline is implemented (storage,
> REST API, conversion, LLM analysis, chunking/embeddings/indexing, hybrid search +
> MCP, and backup/export). See the [implementation plan](docs/planning/implementation-plan.md).

---

## What it does

- **Ingest** documents in many formats (PDF, Office, HTML, images, scanned docs, …)
  via the REST API.
- **Convert** to Markdown using containerised **Docling** (PDF) and **Kreuzberg**
  (everything else, incl. OCR) — routing is configurable.
- **Enrich** with an LLM: **doc-type** classification (first-class types), extraction
  of **identifiers/numbers** (invoice/contract numbers, phone numbers, IBANs, dates,
  amounts, …), a short **summary**, and placement into **folders** (hierarchical,
  n:m) using document-similarity voting.
- **Store** the authoritative record in **Postgres** (documents, folders, doc-types,
  notes, memberships) and the original binary in **MinIO**; project text + summary +
  filter fields into an OpenSearch **document index** and chunk **vectors** into a
  separate **vector index**.
- **Search** via an **MCP server** offering performant **fused hybrid** (keyword +
  semantic, RRF) retrieval, metadata filtering, and folder-tree browsing — plus write
  tools to reorganise documents, folders, doc-types, and notes.
- **Track a timeline** of **audit** events (what the pipeline did and *why*) and **content**
  events (dated facts in the documents — past, future, and recurring), queryable via
  `GET /timeline`, `GET /documents/{id}/timeline`, and an upcoming-view `GET /agenda`.
- **Interchange via OKF**: export the whole archive as an **Open Knowledge Format** bundle
  (`GET /export/okf`) and import one back faithfully (`POST /import/okf`).
- **Back up** everything to a directory tree via a paginated export API + script.

See the [architecture](docs/requirements/03-architecture.md) for details.

## Architecture at a glance

```
Client ──REST(Bearer)──► API ──┬─► Postgres (system of record)
                               ├─► MinIO (originals)
                               ├─► OpenSearch (doc projection + vector index)
                               └─► Redis ──► Worker (ARQ)
                                              ├─► Docling / Kreuzberg (convert)
                                              ├─► LLM (classify type / extract / summarise / place)
                                              ├─► Embeddings
                                              ├─► Postgres (persist)
                                              └─► OpenSearch (project + index)
Agent ──MCP(HTTP, Bearer)──► MCP server ──► Postgres + OpenSearch (fused hybrid search) + Embeddings
```

Providers (LLM + embeddings) are pluggable: **Ollama** (default), **OpenAI**, or
**Azure OpenAI** — all configurable.

## Quick start (Docker)

```bash
cp .env.example .env          # then edit the secrets
docker compose up -d          # starts the full stack

# Pull the default Ollama models (first run only)
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec ollama ollama pull nomic-embed-text
```

- REST API + Swagger UI: <http://localhost:8000/docs>
- MCP endpoint: <http://localhost:8100>
- OpenSearch Dashboards: <http://localhost:5601>
- MinIO console: <http://localhost:9001>

## Local development

```bash
# Install uv: https://docs.astral.sh/uv/
uv sync                       # create venv + install deps (app + dev)
uv run ruff check .           # lint
uv run ruff format --check .  # format check
uv run mypy                   # strict type check
uv run pytest                 # unit tests
```

## Configuration

All behaviour is driven by YAML in [`config/`](config/) with `${ENV}` overrides for
secrets:

| File | Purpose |
|------|---------|
| `config/config.yaml` | API, MCP, security, OpenSearch, Postgres, MinIO, Redis, chunking, dedup, similarity. |
| `config/converters.yaml` | File-type → converter routing (PDF→Docling, else→Kreuzberg). |
| `config/providers.yaml` | LLM + embedding provider selection and models. |
| `config/logging.yaml` | Log levels, colour, categories. |

Prompts and MCP tool descriptions live in [`prompts/`](prompts/).

## Documentation

Start at the [documentation index](docs/README.md).

- [Operations & deployment](docs/operations.md) · [Configuration reference](docs/configuration.md)
- [REST API](docs/api/rest-api.md) · [MCP tools](docs/api/mcp-tools.md) · [Backup](docs/api/backup.md)
- [OKF ↔ SAGA integration roadmap](docs/okf-integration-roadmap.md) (timeline + OKF interchange)
- [Functional requirements](docs/requirements/01-functional-requirements.md) ·
  [Non-functional requirements](docs/requirements/02-non-functional-requirements.md) ·
  [Architecture](docs/requirements/03-architecture.md)
- [Implementation plan](docs/planning/implementation-plan.md)
- [Contributing](CONTRIBUTING.md) · [Guide for AI agents](AGENTS.md)

## License

Apache-2.0 — see [LICENSE](LICENSE).
