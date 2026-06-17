# Non-Functional Requirements

> Status: Draft v1 · Project: **Saga**

These requirements describe **how well** the system must behave: quality
attributes, constraints, and engineering standards.

---

## 1. Technology & language constraints

- **NFR-1 Python** — The application is written in **Python 3.12+**.
- **NFR-2 Full typing** — **All** Python code is fully type-annotated and passes
  `mypy --strict` (or an equivalently strict type checker, e.g. Pyright strict).
- **NFR-3 Package management** — Dependencies are managed with **uv** (`pyproject.toml`
  + `uv.lock`). No `pip install` in scripts/CI without uv.
- **NFR-4 Latest versions** — Use the **latest stable** versions of libraries,
  containers, and tools. Pin via lockfile; keep dependencies current.
- **NFR-5 Project language** — All code, comments, identifiers, docs, commit
  messages, and API contracts are in **English**.

---

## 2. Architecture & deployment

- **NFR-6 Containerised** — Every component runs in a container; a single
  **`docker compose`** brings up the **entire** stack: API, MCP server, worker,
  Docling, Kreuzberg, OpenSearch (+ Dashboards), Postgres, MinIO, Redis,
  (Ollama optional).
- **NFR-7 Service boundaries** — Docling and Kreuzberg run as **separate services**
  consumed only over their HTTP APIs (no in-process coupling).
- **NFR-8 Stateless app tier** — API/MCP/worker processes are stateless; all state
  lives in Postgres (system of record), OpenSearch (search projection), MinIO, and
  Redis so the app tier can scale horizontally.
- **NFR-9 Configurability** — Behaviour is driven by YAML config + env overrides; no
  hard-coded endpoints, models, or limits.

---

## 3. Performance

- **NFR-10 Search latency** — MCP hybrid search target **p95 < 500 ms** for a
  bounded result set (default `top_k ≤ 20`) on a warm index, excluding cold
  embedding-model load.
- **NFR-11 Async ingestion** — Conversion/analysis/embedding run on a **durable
  background queue** (ARQ + Redis); the upload endpoint returns quickly with a job
  reference.
- **NFR-12 Connection reuse** — OpenSearch/MinIO/LLM clients use pooled, reused
  connections; embedding queries reuse a warm model/session.
- **NFR-13 Bounded responses** — All list/search endpoints are paginated with sane
  default and maximum page sizes.

---

## 4. Reliability & observability

- **NFR-14 Retries** — Background jobs retry transient failures with backoff; jobs
  that exhaust retries are marked `failed` with a stored, actionable error message.
- **NFR-15 Actionable errors** — All errors surfaced to clients/logs are
  **meaningful**: what failed, why, and (where safe) how to fix it. No bare
  stack-trace-only responses.
- **NFR-16 Structured logging** — Console logs are **well structured** with
  **colour** and sensible categories/levels, following best practice (human-readable
  in dev, JSON-capable for prod). Correlation/job ids are included.
- **NFR-17 Health checks** — Each service exposes a health/readiness endpoint;
  compose uses healthchecks for ordered startup.

---

## 5. Security

- **NFR-18 Bearer tokens** — REST and MCP endpoints require a Bearer token;
  constant-time comparison; configurable token(s).
- **NFR-19 No secrets in repo** — Secrets only via env/`.env`; `.env` is git-ignored;
  an `.env.example` documents required variables.
- **NFR-20 Input validation** — All inputs validated (Pydantic models); upload size
  limits and content-type checks enforced.
- **NFR-21 Least privilege** — Service credentials (MinIO, OpenSearch) are scoped;
  defaults documented and changeable.

---

## 6. Quality, testing & CI

- **NFR-22 Unit tests** — Unit tests cover core logic (routing, chunking,
  prompt rendering, metadata parsing, search query building). External services are
  mocked.
- **NFR-23 Coverage gate** — CI enforces a minimum coverage threshold (target
  ≥ 80% on core packages).
- **NFR-24 Lint & format** — `ruff` (lint + format) and a strict type check run in CI
  and via pre-commit hooks.
- **NFR-25 CI pipeline** — A **GitHub Actions** pipeline runs lint, type-check,
  tests, and builds container images on PRs and on the default branch.
- **NFR-26 Conventional Commits** — Commits follow **Conventional Commits**; release
  notes/changelog are generated from commit history.
- **NFR-27 Git Flow** — Branching follows **Git Flow** (`main`, `develop`,
  `feature/*`, `release/*`, `hotfix/*`).

---

## 7. Documentation

- **NFR-28 Interface docs** — Comprehensive documentation of the REST API and MCP
  tools (purpose, params, examples) plus usage/operations guides.
- **NFR-29 Swagger UI** — OpenAPI schema + toggleable Swagger UI for the REST API.
- **NFR-30 Prompt & tool descriptions externalised** — LLM prompts and MCP tool
  descriptions live in dedicated **Markdown files** under `prompts/`, version
  controlled and referenced by code (not inlined as string literals).
- **NFR-31 AGENTS.md** — An `AGENTS.md` explains to AI coding agents how to work in
  this repository (conventions, commands, structure).
- **NFR-32 Open source** — The project is structured as a public open-source repo
  (LICENSE, README, CONTRIBUTING, CODE_OF_CONDUCT, issue/PR templates).

---

## 8. Maintainability

- **NFR-33 Modular structure** — Clear separation: API, MCP, pipeline/worker,
  converters, llm, embeddings, storage (postgres/opensearch/minio), search, chunking,
  config, logging.
- **NFR-34 Provider abstraction** — LLM/embedding/converter integrations sit behind
  interfaces so providers can be swapped via config.
- **NFR-35 Reproducibility** — Pinned lockfile + pinned image tags ensure
  reproducible builds; `docker compose up` works from a clean checkout + `.env`.

---

## 9. Interoperability (OKF)

- **NFR-36 OKF conformance & deterministic bundles** — Exported bundles conform to
  **Open Knowledge Format v0.1** (parseable YAML frontmatter, non-empty `type`,
  `index.md`/`log.md` in their reserved roles) and are **deterministic** (stable file
  ordering and zeroed mtimes) so they are byte-stable and git-diffable. SAGA-specific
  data lives in namespaced `saga_*` keys and OKF-ignored sidecar files, so a SAGA
  bundle remains a valid OKF bundle for foreign consumers.
