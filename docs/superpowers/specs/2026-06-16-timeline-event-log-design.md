# Timeline & Event Log — Design

- **Status:** Draft (approved in brainstorming, pending written-spec review)
- **Date:** 2026-06-16
- **Project:** `saga-core`
- **Author:** Brainstormed with the user (Martin)
- **Scope of this document:** the full vision (audit + content timeline + recurrence). The
  *first* implementation plan covers **Phase 1 only** (event store + audit stream).

---

## 1. Context & Motivation

SAGA is a self-organizing archive: the ingestion pipeline classifies a document's type,
extracts values, summarizes, computes similarity, and **places the document into folders on
its own** (`src/saga/pipeline/stages.py`). Today none of these decisions leave a durable
trace, and SAGA records nothing about the *real-world timeline* described inside the
documents.

This design was triggered by the **Open Knowledge Format (OKF)** (Google Cloud, June 2026),
whose `log.md` reserved file records a per-directory, date-grouped history of changes. We want
SAGA to be able to emit such a log on export — but with available data, not invented data.
Exploring that requirement surfaced a richer idea: the log should carry **two distinct kinds
of information**, each tagged so a consumer can choose which "view" to render:

1. **Audit log (archive time)** — *what happened inside the SAGA archive*: placement, moves,
   reclassification, folder creation. Crucially, **with the "why"**: the similar documents and
   `FolderVote`s (and, when available, the LLM's rationale) that drove an automatic placement.
   This makes SAGA's self-organization **explainable**.

2. **Content / timeline log (document time)** — *dates, events, and appointments stated in the
   document content*. Example: you take out an insurance policy on 2026-05-01 but file the
   policy document two weeks later. The document text states the 2026-05-01 conclusion date;
   that real-world date belongs on the timeline, independent of when the file was archived.
   Includes past dated facts, future deadlines/appointments, and recurring obligations.

Both streams live in **one tagged event store** with **one query path**, so the UI, the MCP
server, and (later) the OKF exporter can filter by `category` to render either view.

### Relationship to the OKF export (separate, later spec)

The OKF export — turning the existing `saga-backup export` into an OKF bundle whose per-folder
`log.md` is built from this event store — is **out of scope here** and will get its own
spec → plan cycle. It is a *consumer* of the read layer defined in §5. This document is the
prerequisite data source.

---

## 2. Goals & Non-Goals

### Goals

- A single, append-friendly **event store** in Postgres (system of record), mirrored as a
  typed Pydantic model.
- An **audit stream** emitted deterministically (no LLM) from pipeline stages and API
  mutations, capturing the rationale behind automatic placements.
- A **content/timeline stream** extracted from document text by the LLM (past, future,
  recurring), each event carrying its own real-world date and a source quote.
- **One read/query layer** (`TimelineService`) shared by REST, MCP, and the future exporter,
  filterable by category/type/document/folder/time.
- Configuration-driven behaviour (recurrence horizon, rationale fan-out, sort/pagination),
  per saga-core conventions.

### Non-Goals

- **Storing events in OKF format in the database.** OKF is an interchange projection at the
  boundary, never the storage model. The DB schema is SAGA's own.
- The **OKF export / `log.md` generation** itself (separate spec).
- A **reminders / notification** subsystem. The data model must not preclude it, but building
  it is future work.
- Changing OpenSearch projections or the hybrid-search path.
- A UI timeline view (frontend) — this spec delivers the API/MCP surface; the `saga-ui` view
  is future work.

---

## 3. Domain Model & `events` Table

A new append-only table `events` (new Alembic migration), mirrored as a Pydantic `Event` in
`src/saga/core/models.py`. Postgres remains the system of record; OpenSearch is untouched.

### 3.1 Columns

| Column        | Type          | Meaning |
|---------------|---------------|---------|
| `event_id`    | uuid PK       | |
| `category`    | text          | `audit` \| `content` — the filter tag selecting "which view" |
| `event_type`  | text          | see taxonomy in §3.2 |
| `document_id` | uuid, null    | the document the event concerns (content: always; audit: usually) |
| `folder_id`   | uuid, null    | scope for the per-folder view (audit folder/target events) |
| `occurred_at` | timestamptz, null | **event time** — content: real-world date from text; audit: equals `recorded_at` |
| `recorded_at` | timestamptz, not null, default now() | **archive time** — when the row was written |
| `actor`       | text          | `pipeline` \| `agent` \| `user` \| `extraction` |
| `summary`     | text          | human-readable one-liner for `log.md` / timeline rows |
| `confidence`  | real, null    | content only (LLM extraction confidence) |
| `dedupe_key`  | text, null    | deterministic key to suppress duplicate audit rows on re-ingest (§4.3) |
| `details`     | jsonb         | type-specific payload (§3.3) |

Indexes: `category`, `document_id`, `folder_id`, `occurred_at`, `event_type`, and a unique
partial index on `dedupe_key` (where not null) to enforce audit de-duplication.

### 3.2 `event_type` taxonomy

- **audit:** `doc_ingested`, `placement`, `move`, `reclassification`, `folder_created`,
  `folder_renamed`
- **content:** `dated_fact` (past), `appointment` (future — covers appointments and
  deadlines), `recurring` (a recurrence rule)

### 3.3 `details` payload examples

```jsonc
// audit / placement
{
  "similar":     [{ "document_id": "...", "title": "...", "score": 0.83 }],
  "votes":       [{ "folder_id": "...", "score": 0.71 }],
  "reason":      "<LLM sentence, when the agentic path provided one>",
  "assignments": ["folderId1"],
  "primary":     "folderId1"
}

// audit / reclassification
{ "from_doc_type": "letter", "to_doc_type": "insurance_policy" }

// audit / move
{ "from_folders": ["..."], "to_folders": ["..."], "primary": "..." }

// content / appointment  (future — covers appointments and deadlines)
{ "source_quote": "Die Police endet am 30.04.2027.", "value_key": "policy_end", "recurrence": null }

// content / recurring
{ "source_quote": "jährlich kündbar zum 01.05.",
  "recurrence": { "rrule": "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1", "anchor": "2026-05-01" } }
```

### 3.4 Two time axes (key semantic)

- `occurred_at` = when the event semantically happened or will happen. For content events this
  is the real-world date parsed from the text (or the anchor for recurring rules). For audit
  events it equals `recorded_at`.
- `recorded_at` = when the row was written (archive time).

The audit view sorts by `recorded_at`; the timeline view sorts by `occurred_at`.

### 3.5 Mutability (key semantic)

- **Audit events are immutable history** — written once, never modified or deleted in normal
  operation.
- **Content events are a derivable projection** of the document — on re-analysis of a document
  its content events are **replaced** (delete + reinsert for that `document_id`), exactly as
  OpenSearch is a rebuildable projection. The audit history is unaffected.

### 3.6 Recurrence representation

Store the **rule once** (a single `recurring` event with an RRULE in `details`), never
pre-materialized instances. Future occurrences are expanded **on read** within a bounded,
configurable horizon (§5). This keeps the table small and avoids stale materialized rows.

---

## 4. Audit Stream (Phase 1 — deterministic, no LLM)

### 4.1 EventRecorder

A new thin service `EventRecorder` sits over `PostgresStore.append_event`. Pipeline stages and
API mutations call it; stage logic is otherwise unchanged (events are a cross-cutting concern,
not scattered inline SQL).

### 4.2 Emission points

| Where | `event_type` | `actor` | Rationale source |
|-------|--------------|---------|------------------|
| `place_in_folder` (`stages.py:290`) | `placement` | `pipeline` | `similar` + `votes` available in the stage; LLM `reason` when the agentic path returns one |
| `classify_doc_type` (`stages.py:93`) | `reclassification` | `pipeline` | previous → new doc_type |
| Manual move (API service, `set_document_folders`) | `move` | `user` / `agent` | from/to folders |
| Folder CRUD (`create_folder`, rename) | `folder_created` / `folder_renamed` | `user` / `pipeline` | — |
| Ingestion start | `doc_ingested` | `pipeline` | — |

### 4.3 Rationale capture (the "why")

`place_in_folder` already receives `similar` / `votes` (produced immediately before in
`compute_similarity`, `stages.py:145-182`). The `placement` event records the top-N similar
documents (id, title, score) + the folder votes + the LLM rationale sentence (when the agentic
path provides one) in `details`. The `summary` then reads, e.g.:
*"Placed in Versicherungen/2026 — similar to 'KFZ-Police 2025', 'Hausrat 2024'."*
Top-N is configurable.

### 4.4 Actor derivation

The existing `assigned_by` parameter (`"llm"`, and going forward `"user"` / `"agent"`) maps to
`actor`. The pipeline path records `pipeline`.

### 4.5 Idempotency / re-ingest

The pipeline is idempotent and may re-run stages. Each audit event gets a deterministic
`dedupe_key` (e.g. `placement:<doc_id>:<sorted_folder_ids>`). A unique partial index makes a
re-emitted identical event a no-op (`ON CONFLICT DO NOTHING`); only a genuine change (different
folders, different doc_type) writes a new row. Stage idempotency is preserved.

### 4.6 Fault tolerance

Writing an event must **never** fail ingestion. Errors are logged (`saga.events`) and the
pipeline continues — consistent with the analyzer's "one failed step ≠ failed document" policy
(FR-18).

---

## 5. Content Stream (Phase 2 — LLM extraction)

### 5.1 `extract_timeline` analyzer step

A new analyzer step `extract_timeline`, directly analogous to `extract_values`
(`analyzer.py:260-281`): `saidex` structured extraction in **JSON mode** (more reliable for
small local models), with the prompt externalized to `prompts/analysis/timeline-extraction.md`
(never inlined, per saga-core conventions). It runs in the `analyzing` phase, after
`extract_values`.

### 5.2 Schema (`src/saga/llm/schemas.py`)

`TimelineExtraction { events: list[TimelineEvent] }`, where each `TimelineEvent` has:

| Field | Meaning |
|-------|---------|
| `kind` | `past` \| `future` \| `recurring` → maps to `event_type` `dated_fact` / `appointment` / `recurring` |
| `description` | short event text ("Police abgeschlossen") |
| `date` | ISO date of the event (→ `occurred_at`); for `recurring`, the anchor |
| `recurrence` | optional RRULE (only for `recurring`) |
| `source_quote` | supporting quote from the text (traceability) |
| `confidence` | 0–1 |

### 5.3 Persistence

Results are written as `category=content` events (`actor=extraction`, `confidence` set,
`details.source_quote` / `recurrence`). On re-analysis: delete all content events for that
`document_id`, then reinsert (the "derivable projection" rule from §3.5).

### 5.4 Date normalization

The LLM returns ISO dates. Relative expressions ("in two weeks") should be resolved against a
reference date stated in the document; otherwise dropped. **Matches with no resolvable date are
discarded** (no `occurred_at` ⇒ no timeline event). This keeps the timeline clean.

### 5.5 Robustness

If the step fails, it is logged and the document simply has no content events — no ingestion
abort (same policy as every analyzer step, FR-18).

### 5.6 Boundary vs `extracted_values`

`ExtractedValue` remains for identifiers/numbers; `extract_timeline` is solely for **dated**
events. No conflation.

---

## 6. Read / Query Layer (one path for both streams)

A `TimelineService` over a new `PostgresStore.query_events(...)` — *the* single read path all
consumers share.

### 6.1 Query parameters (filters = "which view")

- `category` (`audit` | `content` | both) — the central switch
- `event_type` (multi-select)
- `document_id` and/or `folder_id` (folder including descendants, resolved via
  `ancestor_ids` / `parents_map`, as the pipeline already does)
- time window over `occurred_at`; sort: audit view by `recorded_at` desc, timeline view by
  `occurred_at`
- **recurrence expansion:** for future views, `recurring` rules are expanded on-read into a
  bounded, configurable horizon (e.g. 12 months); without a horizon the query returns only the
  rule itself

### 6.2 Folder scoping (for the future per-folder `log.md`)

Audit folder events carry `folder_id` directly. Content events are mapped to a folder via the
**current** `document_folders` membership — resolved at read time, not frozen at write time
(membership is n:m and can change).

### 6.3 Consumers (in this spec)

- **REST:** new route `GET /timeline` (filters as query params) + `GET /documents/{id}/timeline`.
  Registered on the app, with OpenAPI docs and tests, per saga-core conventions.
- **MCP:** a new tool (e.g. `get_timeline`), description in `prompts/mcp/<tool>.md`, so agents
  can answer "what's coming up in the next weeks / what happened to document X".

### 6.4 Consumers (later specs)

- **OKF export:** per-folder `log.md` = `query_events(folder_id=…)` grouped by date; the
  `category` is written as a per-entry marker so a reader can distinguish the two views.

All thresholds (horizon, rationale top-N, default sort/page-size) are config-driven
(`config/config.yaml`), never hard-coded.

---

## 7. Phasing, Configuration & Testing

### 7.1 Implementation phases (one design doc, separate plans; first plan = Phase 1 only)

- **Phase 1 — foundation + audit (no LLM):** `events` table + migration, `Event` model,
  `PostgresStore.append_event` / `query_events` (+ `dedupe_key`), `EventRecorder`, emission in
  `place_in_folder` / `classify_doc_type` / folder CRUD / ingestion start with rationale,
  `TimelineService`, `GET /timeline` + `GET /documents/{id}/timeline`, MCP `get_timeline`.
  **Immediately usable audit trail.**
- **Phase 2 — content stream (LLM):** `TimelineEvent` schema,
  `prompts/analysis/timeline-extraction.md`, `extract_timeline` step, pipeline wiring in the
  `analyzing` phase, persistence as `content` events (delete + reinsert on re-analysis),
  `past` / `future`.
- **Phase 3 — recurrence + consumer polish:** RRULE storage + on-read expansion over the
  horizon, the upcoming-appointments view. (The OKF export / `log.md` and any reminders are
  *separate* later specs that only consume this read path.)

### 7.2 Configuration (`config/config.yaml`, no hard-coding)

Recurrence horizon, rationale top-N, default timeline sort and page size.

### 7.3 Testing (≥80% on core packages; external services mocked)

- **Phase 1:** `EventRecorder` / `dedupe_key` (duplicate suppression on re-ingest);
  `query_events` filters (category / type / folder-with-descendants / time window); rationale
  serialization; per-stage emission unit tests (LLM/DB mocked); route + MCP-tool tests.
- **Phase 2:** `extract_timeline` with a mocked analyzer (datable → event, dateless →
  discarded); delete + reinsert on re-analysis.
- **Phase 3:** RRULE expansion inside / outside the horizon.

### 7.4 Definition of Done (per phase, from `CLAUDE.md`)

Typed; `ruff` clean; `mypy` strict green; `pytest` green; `docker compose build api worker`
exits 0; configuration-driven; errors from `saga.core.errors`; structured logs; requirement
IDs referenced; docs updated where behaviour changed.

---

## 8. Open Questions / Future Work

- **Requirement IDs:** new FR/NFR IDs for the event log to be assigned in `docs/requirements/`
  when the Phase 1 plan is written.
- **RRULE library vs. minimal subset:** Phase 3 decides whether to depend on a dateutil-style
  RRULE library or support a constrained subset; deferred to that phase.
- **Reminders/agenda surface** (UI, notifications) — explicitly out of scope; the schema
  (`occurred_at`, `recurring`) is designed not to preclude it.
- **OKF export spec** — the immediate logical follow-up that consumes §6.
