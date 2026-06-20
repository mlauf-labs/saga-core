# Metrics & monitoring

SAGA exposes archive statistics through **two surfaces** that share one computation path:

1. **Prometheus `/metrics` endpoints** — for the maintainer's own Prometheus/Grafana to
   scrape and chart history. SAGA does **not** ship Prometheus or Grafana; it only exposes
   the endpoints.
2. **A JSON `/stats` endpoint** — a point-in-time snapshot consumed by the SAGA UI Statistics
   tab. The UI never talks to Prometheus.

Metrics are best-effort: instrumentation and the Redis aggregate writes are wrapped so they
can never break ingestion or an API request. If a backend is unreachable, its fields degrade
to `null`/omitted and the rest still render.

## Endpoints

| Endpoint | Process | Port | Auth | Purpose |
|----------|---------|------|------|---------|
| `GET /stats` | API (`saga-api`) | 8000 | Bearer token | JSON snapshot + pipeline aggregates (UI) |
| `GET /metrics` | API (`saga-api`) | 8000 | none | Prometheus snapshot gauges |
| `GET /metrics` | Worker (`saga-worker`) | 9000 | none | Prometheus cumulative pipeline/LLM metrics |
| `GET /metrics` | Agents (`saga-agents`) | 8099 | none | Prometheus agent run/proposal/token metrics |
| `GET /stats` | Agents (`saga-agents`) | 8099 | (agents auth) | JSON agent snapshot (UI, via the BFF) |

`/metrics` is intentionally unauthenticated (Prometheus convention) — restrict it at the
network level. The worker has no HTTP server of its own, so it starts a dedicated Prometheus
server on `metrics.worker_port` (default **9000**); both the API gauges and the worker
counters must be scraped to get the full picture.

> **Why two saga-core targets:** snapshot gauges (inventory, storage, queue depth) are
> recomputed live in the **API** process, which holds the backend clients. Cumulative
> pipeline/LLM metrics (stage timings, tokens, cost) are produced in the **worker** and
> exposed on its own port. The UI gets per-stage min/max/avg from a compact Redis aggregate
> via `/stats`; Prometheus gets the histograms (and thus quantiles + history) from the worker.

## Example `prometheus.yml`

A minimal scrape config covering all three targets (service names match the workspace
`docker-compose.yml`; adjust hosts/ports to your deployment):

```yaml
global:
  scrape_interval: 30s

scrape_configs:
  - job_name: saga-api
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]

  - job_name: saga-worker
    metrics_path: /metrics
    static_configs:
      - targets: ["worker:9000"]

  # Only if saga-agents is part of the stack.
  - job_name: saga-agents
    metrics_path: /metrics
    static_configs:
      - targets: ["agents:8099"]
```

The worker metrics port must be reachable by Prometheus — expose/publish
`metrics.worker_port` on the `saga-worker` service if Prometheus runs outside the Docker
network.

## Metric reference

All series use the `saga_` prefix.

### API process (`saga-api:8000/metrics`) — snapshot gauges

Recomputed on every scrape from Postgres / OpenSearch / MinIO / Redis.

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `saga_documents_total` | gauge | — | Total documents |
| `saga_documents_by_status` | gauge | `status` | Documents per ingestion status |
| `saga_documents_by_doc_type` | gauge | `doc_type` | Documents per doc-type |
| `saga_documents_by_mime` | gauge | `mime_type` | Documents per MIME type |
| `saga_folders_total` | gauge | — | Total folders |
| `saga_doc_types_total` | gauge | — | Total doc-types |
| `saga_events_total` | gauge | `category` | Events per category |
| `saga_document_size_bytes_sum` | gauge | — | Sum of original document sizes |
| `saga_document_size_bytes_max` | gauge | — | Largest document size |
| `saga_document_size_bytes_avg` | gauge | — | Average document size |
| `saga_chunks_total` | gauge | — | Chunks in the vector index |
| `saga_storage_bytes` | gauge | `backend` | Storage usage per backend (`postgres`, `opensearch`, `minio`, `redis`) |
| `saga_queue_depth` | gauge | — | Pending ingestion jobs |

### Worker process (`saga-worker:9000/metrics`) — cumulative metrics

Incremented during ingestion runs; reset on process restart (history lives in Prometheus).

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `saga_pipeline_stage_duration_seconds` | histogram | `stage` | Duration of a single pipeline stage |
| `saga_pipeline_total_duration_seconds` | histogram | — | End-to-end ingestion duration per document |
| `saga_ingest_total` | counter | `result` | Completed ingestion runs by result (`success`/`failed`) |
| `saga_converter_duration_seconds` | histogram | `converter` | Conversion duration (`docling`/`kreuzberg`) |
| `saga_llm_tokens_total` | counter | `step`, `model`, `kind` | LLM tokens by pipeline step, model, and kind (`prompt`/`completion`) |
| `saga_llm_cost_usd_total` | counter | `model` | Estimated LLM cost in USD (only for models priced in `metrics.prices`) |
| `saga_llm_call_duration_seconds` | histogram | `model` | LLM call latency by model |

Pipeline stages (label values for `saga_pipeline_stage_duration_seconds`): `convert`,
`classify_doc_type`, `extract_values`, `extract_timeline`, `summarize`, `compute_similarity`,
`place_in_folder`, `index_chunks`.

### saga-agents (`saga-agents:8099/metrics`)

Present only when saga-agents is part of the stack.

| Metric | Type | Labels | Meaning |
|--------|------|--------|---------|
| `saga_agent_runs_total` | counter | `agent_id`, `trigger`, `result` | Agent runs by agent, trigger, and result |
| `saga_agent_run_duration_seconds` | histogram | `agent_id` | Agent run duration |
| `saga_agent_inflight` | gauge | — | Currently executing agent runs |
| `saga_agent_concurrency_limit` | gauge | — | Configured global concurrency limit |
| `saga_agent_proposals_total` | counter | `state` | Proposals by terminal state |
| `saga_agent_tokens_total` | counter | `agent_id`, `model`, `kind` | LLM tokens by agent, model, and kind |

## Configuration

saga-core, under `metrics:` in `config/config.yaml` (see the
[configuration reference](configuration.md)):

| Key | Default | Meaning |
|-----|---------|---------|
| `metrics.enabled` | `true` | Master switch. When `false`, the API skips `/metrics` and the worker does not start its metrics server. |
| `metrics.worker_port` | `9000` | Port for the worker's Prometheus HTTP server. |
| `metrics.stuck_threshold_seconds` | `3600` | Age above which a document in an intermediate status is considered stuck. |
| `metrics.prices` | `{}` | Per-model price table (`{model: {prompt_per_1k, completion_per_1k}}`) driving `saga_llm_cost_usd_total`. Models without a price emit no cost series. |

## Assumptions

- **One uvicorn worker per API container**, so the `prometheus_client` registry is correct.
  If you scale the API process out, configure `PROMETHEUS_MULTIPROC_DIR`.
- LLM responses surface `usage_metadata` (true for Ollama / OpenAI-compatible via the LiteLLM
  gateway). When absent, token counts stay at 0 rather than failing.
