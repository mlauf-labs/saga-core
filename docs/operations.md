# Operations & deployment guide

This guide covers running Saga in development and hardening it for production.

## Components

`docker compose` brings up the whole stack (NFR-6):

| Service | Port | Role |
|---------|------|------|
| `api` | 8000 | REST API + Swagger UI. |
| `mcp` | 8100 | MCP server (Streamable HTTP) for agents. |
| `worker` | – | ARQ ingestion worker (convert → classify → extract → summarise → similarity → place → project + index). |
| `docling` | 5001 | PDF conversion (OCR/layout). |
| `kreuzberg` | 8001→8000 | Non-PDF + image/scanned OCR conversion. |
| `postgres` | 5432 | System of record: documents, folders, doc-types, notes, memberships. |
| `opensearch` | 9200 | Search projection: document + vector indices. |
| `opensearch-dashboards` | 5601 | Index inspection UI. |
| `minio` | 9000 / 9001 | Object storage for originals (+ console). |
| `redis` | 6379 | ARQ job queue. |
| `ollama` | 11434 | Default local LLM + embeddings. |

## Quick start

```bash
cp .env.example .env          # edit secrets
docker compose up -d
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec ollama ollama pull nomic-embed-text
```

- REST + Swagger: <http://localhost:8000/docs> · health: `GET /health`
- MCP endpoint: `http://localhost:8100/mcp` (Bearer required)

## Ingestion lifecycle

Upload returns `202` with a `document_id`; processing is asynchronous. Poll
`GET /documents/{id}/status` until `ready` (or `failed`, which stores an actionable
error). Status flow: `pending → converting → classifying_type → analyzing →
summarizing → classifying → indexing → ready`.

## Scaling

The app tier is stateless (NFR-8): scale `worker` for ingestion throughput and `api`
for request load.

```bash
docker compose up -d --scale worker=4 --scale api=2
```

State lives in Postgres (system of record), OpenSearch (search projection), MinIO and
Redis. Put a reverse proxy / load balancer in front of `api` and `mcp`.

## Resource sizing

The default stack is heavy (OpenSearch + Ollama). Rough minimums for a single host:
**8 CPU / 16 GB RAM**, more for large models or high ingest volume. To reduce the
footprint, point `providers.yaml` at managed OpenAI/Azure endpoints and drop the
`ollama` service.

## Production hardening

### Secrets
- Never commit `.env`. Rotate `SAGA_API_TOKENS` regularly; use long random tokens.
- Supply secrets via your orchestrator's secret store, not plain env files.

### OpenSearch security + TLS
The dev compose sets `DISABLE_SECURITY_PLUGIN=true` for a frictionless start. For
production:
1. Enable the security plugin (remove `DISABLE_SECURITY_PLUGIN`), configure users/roles.
2. Serve OpenSearch over HTTPS; set `opensearch.hosts` to `https://…`,
   `opensearch.verify_certs: true`, and provide real credentials.
3. Restrict network access so only the app tier can reach OpenSearch/MinIO/Redis.

### MinIO
- Set strong `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`; enable TLS (`minio.secure: true`).
- Use a dedicated, least-privilege service account for the `saga-originals` bucket.

### Postgres
- Set a strong `POSTGRES_PASSWORD`; restrict network access to the app tier only.
- Back it up regularly — it is the **system of record** (FR-40); the OpenSearch
  projection is rebuildable, Postgres is not. Tune `postgres.pool_size` /
  `max_overflow` for your worker/API concurrency.

### Transport
- Terminate TLS at a reverse proxy for `api` and `mcp`; both require Bearer tokens
  (FR-35/36), compared in constant time.

### Image pinning (reproducibility)
The committed `docker-compose.yml` tracks `latest` to follow the "newest versions"
policy (NFR-4). For reproducible production deploys (NFR-35), pin every image to a
tested tag or digest in an override file, e.g. `docker-compose.prod.yml`:

```yaml
services:
  opensearch:
    image: opensearchproject/opensearch:2.19.0
  minio:
    image: minio/minio:RELEASE.2026-XX-XXTXX-XX-XXZ
```

Deploy with `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`.

## Observability

Logs are structured (NFR-16): coloured console in dev (`LOG_RENDERER=console`),
JSON in prod (`LOG_RENDERER=json`). Each ingestion log line carries the document id as
`correlation_id`. Health endpoints back compose healthchecks for ordered startup.

### LLM analysis logging

Each metadata-analysis step (doc-type classification, value extraction, summary,
folder placement) logs:

- `analysis_step_start` — `step`, input `chars`.
- `analysis_step_done` / `analysis_step_failed` — `llm_calls` (number of LLM calls in
  the step), `validation_retries` (schema/type retries), `fallback_used`, `elapsed_ms`.
- `llm_correction_sent` — on a retry, the exact field-level correction text sent back
  to the model (truncated). This shows *why* a step is retrying.

If a step is slow, check `elapsed_ms` and `llm_calls`: a high `elapsed_ms` with few
calls means the model itself is slow (raise the model's throughput or lower
`llm.providers.<p>.request_timeout`); many `llm_calls` with `llm_correction_sent`
entries means the model keeps producing invalid output (use a stronger model or a
`llm.fallback_model`).

### Retry layers

A single analysis step can issue multiple LLM calls. Retries are intentionally **not**
stacked: the OpenAI client's own retries are disabled (`max_retries=0`) because the
structured-output library already retries transient/network errors. Validation retries
(`llm.max_primary_retries` / `max_fallback_retries`) re-prompt the model with the
correction text. The ARQ worker additionally retries the whole job up to `max_tries`
on unhandled failures. A too-low `request_timeout` against a slow model multiplies wait
time across these layers — tune the model/endpoint first.

## Backups

Use `saga-backup` to export everything to a directory tree — see
[`api/backup.md`](api/backup.md). For disaster recovery, back up **Postgres** (the
system of record) and the MinIO bucket; the OpenSearch projection can be rebuilt from
those, but snapshotting the indices speeds recovery.

## Changing the embedding model

The kNN dimension is fixed at index creation and must match the embedding model
(FR-27). To change models: update `providers.yaml` + `opensearch.vector_dimension`,
create fresh indices (or a new alias), and re-ingest / reindex. A mismatch is logged
as a warning at worker startup.
