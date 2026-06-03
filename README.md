# DocStore

> A document store for RAG agents — ingest any text-convertible document, enrich it
> with LLM-extracted metadata, index it in OpenSearch (keyword + vector), and let
> agents query it via **hybrid search** over MCP.

[![CI](https://github.com/OWNER/docstore/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/docstore/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

> **Status:** early development. Phase 0 (project bootstrap) is complete; features
> land per the [implementation plan](docs/planning/implementation-plan.md).

---

## What it does

- **Ingest** documents in many formats (PDF, Office, HTML, images, scanned docs, …)
  via the REST API.
- **Convert** to Markdown using containerised **Docling** (PDF) and **Kreuzberg**
  (everything else, incl. OCR) — routing is configurable.
- **Enrich** with an LLM: document **classification**, extraction of **identifiers/
  numbers** (invoice/contract numbers, phone numbers, IBANs, dates, amounts, …),
  and **hierarchical categorisation** (e.g. `Insurance/Health`).
- **Store** originals in **MinIO**, text + metadata in an OpenSearch **document
  index**, and chunk **vectors** in a separate **vector index** referencing the doc.
- **Search** via an **MCP server** offering performant **hybrid** (keyword +
  semantic) retrieval, metadata filtering, and category-tree browsing.
- **Back up** everything to a directory tree via a paginated export API + script.

See the [architecture](docs/requirements/03-architecture.md) for details.

## Architecture at a glance

```
Client ──REST(Bearer)──► API ──┬─► MinIO (originals)
                               ├─► OpenSearch (doc + vector indices)
                               └─► Redis ──► Worker (ARQ)
                                              ├─► Docling / Kreuzberg (convert)
                                              ├─► LLM (classify/extract/categorise)
                                              └─► Embeddings ──► OpenSearch
Agent ──MCP(HTTP, Bearer)──► MCP server ──► OpenSearch (hybrid search) + Embeddings
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
| `config/config.yaml` | API, MCP, OpenSearch, MinIO, Redis, chunking, security. |
| `config/converters.yaml` | File-type → converter routing (PDF→Docling, else→Kreuzberg). |
| `config/providers.yaml` | LLM + embedding provider selection and models. |
| `config/logging.yaml` | Log levels, colour, categories. |

Prompts and MCP tool descriptions live in [`prompts/`](prompts/).

## Documentation

- [Functional requirements](docs/requirements/01-functional-requirements.md)
- [Non-functional requirements](docs/requirements/02-non-functional-requirements.md)
- [Architecture](docs/requirements/03-architecture.md)
- [Implementation plan](docs/planning/implementation-plan.md)
- [Contributing](CONTRIBUTING.md) · [Guide for AI agents](AGENTS.md)

## License

Apache-2.0 — see [LICENSE](LICENSE).
