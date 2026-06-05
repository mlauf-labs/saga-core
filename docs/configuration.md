# Configuration reference

All behaviour is driven by YAML files in [`config/`](../config) with `${ENV_VAR}` and
`${ENV_VAR:-default}` placeholders resolved from the environment at load time. Secrets
are **never** committed — supply them via environment variables / `.env` (NFR-19). See
[`.env.example`](../.env.example) for the required variables.

## Files

| File | Purpose |
|------|---------|
| `config/config.yaml` | API, MCP, security, OpenSearch, MinIO, Redis, chunking, dedup. |
| `config/converters.yaml` | File-type → converter routing and service endpoints. |
| `config/providers.yaml` | LLM + embedding provider selection and model settings. |
| `config/logging.yaml` | Log level, renderer (colour/JSON), per-category levels. |

## `config.yaml`

### `api`
| Key | Default | Description |
|-----|---------|-------------|
| `host` / `port` | `0.0.0.0` / `8000` | REST API bind address. |
| `enable_swagger` | `true` | Toggle the Swagger UI / OpenAPI (FR-34). |
| `max_upload_bytes` | `104857600` | Max upload size (100 MiB) (NFR-20). |
| `cors_allow_origins` | `["*"]` | Allowed CORS origins for browser UIs. Comma-separated string (env `API_CORS_ALLOW_ORIGINS`) or list; `*` allows all. Restrict in production. |
| `cors_allow_credentials` | `true` | Allow credentials in CORS requests (env `API_CORS_ALLOW_CREDENTIALS`). |
| `pagination.default_page_size` | `25` | Default list page size. |
| `pagination.max_page_size` | `200` | Hard cap for list/export page size. |

### `mcp`
| Key | Default | Description |
|-----|---------|-------------|
| `host` / `port` | `0.0.0.0` / `8100` | MCP server bind address. |
| `transport` | `streamable-http` | MCP transport. |
| `default_top_k` / `max_top_k` | `10` / `50` | Search result bounds (NFR-10). |

### `security`
| Key | Description |
|-----|-------------|
| `bearer_tokens` | Comma-separated Bearer tokens for REST **and** MCP (FR-35/36). From `${DOCSTORE_API_TOKENS}`. |

### `opensearch`
| Key | Default | Description |
|-----|---------|-------------|
| `hosts` | `http://opensearch:9200` | Comma-separated host URLs. |
| `username` / `password` | `admin` / `${OPENSEARCH_PASSWORD}` | Credentials (when security is on). |
| `verify_certs` | `false` | Verify TLS certificates (enable in prod). |
| `document_index` / `chunk_index` | `documents` / `document_chunks` | Index names (FR-25). |
| `hybrid_pipeline` | `docstore-hybrid` | Search pipeline name. |
| `vector_dimension` | `768` | **Must match the embedding model** (FR-27). Changing requires a reindex. |
| `vector_space_type` / `vector_engine` | `cosinesimil` / `faiss` | kNN metric and engine. |
| `knn_ef_construction` / `knn_m` | `256` / `16` | HNSW build parameters. |
| `hybrid_weights` | `[0.4, 0.6]` | `[lexical, semantic]` combination weights. |
| `hybrid_normalization` / `hybrid_combination` | `min_max` / `arithmetic_mean` | Score fusion. |

### `minio`
| Key | Default | Description |
|-----|---------|-------------|
| `endpoint` | `minio:9000` | MinIO endpoint. |
| `access_key` / `secret_key` | `${MINIO_ACCESS_KEY}` / `${MINIO_SECRET_KEY}` | Credentials. |
| `secure` | `false` | Use TLS (enable in prod). |
| `bucket` | `docstore-originals` | Bucket for original binaries (FR-9). |

### `redis`
| Key | Default | Description |
|-----|---------|-------------|
| `url` | `redis://redis:6379/0` | ARQ job queue (NFR-11). |

### `chunking`
| Key | Default | Description |
|-----|---------|-------------|
| `max_tokens` | `512` | Max chunk size in tokens (FR-6). |
| `overlap_tokens` | `64` | Token overlap between chunks. |
| `tokenizer` | `cl100k_base` | tiktoken encoding for token counting. |

### `dedup`
| Key | Default | Description |
|-----|---------|-------------|
| `on_duplicate` | `replace` | `reject` \| `replace` \| `allow` on identical content (FR-13). |
| `document_id_on_update` | `keep` | `keep` \| `new` document id on replace (FR-11). |

## `converters.yaml`

Per-service settings under `services.<name>` (`base_url`, `timeout_seconds`,
`output_format`, `ocr.enabled`, `ocr.languages`, `api_key`, `max_retries`) and routing
under `routing` (`default`, `by_extension`, `by_mime_type`). Default policy:
**PDF → Docling**, everything else → **Kreuzberg** (FR-3).

## `providers.yaml`

`llm` and `embeddings` sections each have a `provider` selector (`ollama` | `openai` |
`azure`) and a `providers.<name>` block. Default: **Ollama** (local, no API keys). The
embedding `dimension` **must** equal `opensearch.vector_dimension` (FR-27).

Structured metadata extraction uses the `llm-structured-output` library (tool-calling
with automatic retry on schema/type errors). Relevant `llm` keys:

| Key | Default | Description |
|-----|---------|-------------|
| `max_input_chars` | `12000` | Max document characters sent per analysis call. |
| `max_primary_retries` | `3` | Validation-retry attempts on the primary model. |
| `max_fallback_retries` | `3` | Validation-retry attempts on the fallback model. |
| `fallback_model` | `` (off) | Optional fallback model (same provider) used when the primary exhausts its retries. |
| `category_cache_ttl_seconds` | `300` | TTL of the in-memory existing-category catalog fed to categorisation; DB writes invalidate it immediately (FR-16). |
| `max_categories_in_prompt` | `200` | Max existing category paths shown to the model as context. |

Per-provider `max_output_tokens` and `request_timeout` bound generation length and call
duration. Models must support **tool calling** (e.g. `llama3.2:3b`, `llama3.1:8b`,
GPT-4o family). **Ollama** is accessed via its **OpenAI-compatible** `/v1` endpoint
(the structured-output library issues OpenAI-style tool calls), so the configured
`base_url` should be the Ollama root (e.g. `http://ollama:11434`); `/v1` is appended
automatically.

## `logging.yaml`

`level` (e.g. `INFO`), `renderer` (`console` for coloured dev output, `json` for prod),
and per-category levels under `categories` (NFR-16).
