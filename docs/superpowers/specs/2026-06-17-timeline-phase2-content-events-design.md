# Timeline Phase 2 — Content / Timeline Event Extraction — Design

- **Status:** Draft (approved in brainstorming, pending written-spec review)
- **Date:** 2026-06-17
- **Project:** `saga-core`
- **Builds on:** [timeline event-log design](2026-06-16-timeline-event-log-design.md) §5 (Phase 2),
  Phase 1 (event store + audit stream, already implemented). Consumed by the
  [OKF export](2026-06-17-okf-export-design.md) `log.md` automatically.

---

## 1. Context

Phase 1 gave SAGA a tagged event store and an **audit** stream. Phase 2 adds the **content /
timeline** stream: dates, events, appointments, and recurring obligations **extracted from the
document text** by the LLM. Example: an insurance policy filed weeks late, but whose text states
the conclusion date (and a coverage period, and an annual renewal) — those belong on the
timeline at their *real-world* date, independent of when the document was archived.

Content events are a **derivable projection** of the document (like the OpenSearch index): on
re-analysis they are replaced. They flow into the existing read path (`TimelineService` /
`query_events`) and therefore appear automatically in the per-folder `log.md` of the OKF export
— no export change required.

### Decisions (from brainstorming)
- Phase 2 extracts **all three kinds**: `past` (dated_fact), `future` (appointment), and
  **`recurring`** (rule captured now; on-read expansion is Phase 3).
- The LLM **judges relevance** via a per-event `confidence`; a **configurable threshold** filters.
- Events carry an optional **`end_date`** (period or recurrence end; null = open/unknown), in
  addition to the start `date`. This generalises to bounded periods (e.g. coverage terms).
- **No new migration** — content events use the existing `events` table; `end_date`/`recurrence`
  live in the `details` JSONB.

---

## 2. Goals & Non-Goals

### Goals
- A new `extract_timeline` analyzer step + `TimelineExtraction`/`TimelineEvent` saidex schema.
- Filtering (drop dateless events; drop events below a configurable confidence threshold).
- Atomic persistence of content events (`replace_content_events`: delete + reinsert per document).
- Pipeline wiring in the `analyzing` phase, after `extract_values`.
- Robustness: never aborts ingestion; transient failure preserves prior events.

### Non-Goals (→ Phase 3 or later)
- **On-read recurrence expansion** (RRULE → individual occurrences within a horizon).
- **RRULE validation** (Phase 2 stores the rule as-is; Phase 3 validates/expands).
- An "upcoming / agenda" view and reminders/notifications.
- A bulk **backfill** of existing documents (they gain content events on the normal re-analyze path).
- Any change to the OKF export (content events appear there automatically via `TimelineService`).

---

## 3. Schema (`src/saga/llm/schemas.py`)

`TimelineExtraction { events: list[TimelineEvent] }`. Lenient like the sibling schemas
(`model_config = _LLM_MODEL_CONFIG`, `extra="ignore"`, tolerant coercion), so small local models
don't trigger needless validation retries.

`TimelineEvent` fields:

| Field | Type | Meaning |
|-------|------|---------|
| `kind` | str | `past` \| `future` \| `recurring` (default `past`); → `event_type` |
| `description` | str | short event text → `Event.summary` |
| `date` | str | start/event date, ISO `YYYY-MM-DD` → `occurred_at` (anchor for recurring) |
| `end_date` | str \| None | optional ISO end date; null = open/unknown (periods + recurrence end) |
| `recurrence` | str \| None | optional RRULE pattern (only meaningful for `recurring`) |
| `source_quote` | str | supporting quote from the document text |
| `confidence` | float | 0–1, the LLM's relevance/confidence |

**`kind` → `event_type` mapping:** `past` → `dated_fact`, `future` → `appointment`,
`recurring` → `recurring` (the `EventType` values already exist from Phase 1). Unknown/empty
`kind` defaults to `past`/`dated_fact`.

**Field semantics:**
- Point event (most `past`/`future`): `date` set, `end_date` null.
- Period (e.g. an insurance coverage term): `date` = start, `end_date` = end.
- Recurring: `date` = start/anchor, `end_date` = end of recurrence (null = open), `recurrence`
  = the RRULE *pattern* (FREQ/INTERVAL/…), **without** `UNTIL` — the end is carried by `end_date`.

---

## 4. `extract_timeline` analyzer step (`src/saga/llm/analyzer.py`)

A new method `DocumentAnalyzer.extract_timeline(content, trace_callbacks) -> TimelineExtraction | None`,
directly analogous to `extract_values` (`analyzer.py:260-281`): saidex structured extraction in
**JSON mode** (more reliable for small local models), with the prompt externalised to
`prompts/analysis/timeline-extraction.md` (never inlined). Returns `None` on failure (FR-18).

**Prompt guidance** (`prompts/analysis/timeline-extraction.md`): extract dated events,
appointments, deadlines, and recurring obligations described in the text; return ISO dates only
(resolve relative expressions like "in two weeks" **only** against a reference date stated in the
document — otherwise omit the event); assign a relevance `confidence` per event; for recurring
obligations, provide an RRULE pattern + start `date` (+ `end_date` when an end is stated). It does
**not** restate raw identifiers/numbers (those are `extract_values`' job — §3.6 boundary of the
Phase 1 spec).

---

## 5. Filtering (in the stage)

After extraction, the stage keeps an event only if:
1. its `date` parses to a valid date (no `occurred_at` ⇒ dropped — keeps the timeline clean), and
2. its `confidence >= config.timeline.content_min_confidence`.

`end_date`, when present, must also parse to a valid date; an unparseable `end_date` is dropped
(set to null) but the event is kept.

---

## 6. Persistence (`src/saga/storage/postgres.py`)

**No new migration.** A content event maps onto the existing `events` columns:

| Column | Value |
|--------|-------|
| `category` | `content` |
| `event_type` | from `kind` (`dated_fact` / `appointment` / `recurring`) |
| `document_id` | the source document |
| `folder_id` | **null** (folder resolved at read time via membership — §7) |
| `occurred_at` | the start `date` (midnight UTC) |
| `recorded_at` | now |
| `actor` | `extraction` |
| `summary` | `description` |
| `confidence` | the LLM confidence |
| `dedupe_key` | null (content events are replaced, not de-duplicated) |
| `details` | `{ "source_quote": …, "end_date": … (if set), "recurrence": … (if recurring) }` |

New store method `replace_content_events(document_id, events)`: in one transaction, **delete**
all rows where `category = 'content' AND document_id = X`, then **insert** the new events. Audit
events are untouched. This is the "derivable projection" rule (Phase 1 spec §3.5): re-analysis
keeps the content timeline in sync with the document.

---

## 7. Document → folder model (read-time, unchanged from Phase 1)

Content events store `document_id` and **no** `folder_id`. The folder association is resolved at
**read time** from the current `document_folders` membership (the Phase 1 §6.2 design + the C1
follow-up: `TimelineService` / `query_events` match events for documents currently in a folder
subtree). Consequences: a document in multiple folders surfaces its events in all of them; moving
a document makes its events follow automatically; unfiled documents' events exist (document- and
global-timeline) but appear under no folder `log.md`.

---

## 8. Pipeline wiring (`src/saga/pipeline/`)

New stage `extract_timeline` in `stages.py` (mirrors the `extract_values` stage): calls the
analyzer, applies §5 filtering, builds the content `Event` objects, and calls
`db.replace_content_events`. Returns the count.

`tasks.py` adds a `tracer.step_span("extract_timeline", …)` block in the **`analyzing`** phase,
**directly after** `extract_values` (same `DocumentStatus.ANALYZING`). It reads
`config.timeline.content_min_confidence`. Existing documents gain content events via the normal
**re-analyze** path — no separate backfill.

---

## 9. Configuration

`TimelineConfig.content_min_confidence: float = 0.5` (in `src/saga/core/config.py` +
`config/config.yaml`), config-driven per saga-core conventions.

---

## 10. Robustness (FR-18)

- LLM step fails / returns `None` → logged; `replace_content_events` is **not** called, so prior
  content events are preserved (a transient failure never wipes good data). Ingestion continues.
- LLM step succeeds but returns **no** events → `replace_content_events` with an empty list (=
  delete the document's prior content events; the latest analysis legitimately found none).
- A DB error in `replace_content_events` is logged; ingestion continues.
- RRULE strings are stored **as-is** (no Phase-2 validation); Phase 3 validates/expands them.

---

## 11. Testing

≥80% coverage on core packages; LLM and DB mocked where possible.
- **Schema:** `TimelineExtraction`/`TimelineEvent` lenient parsing (defaults, coercion).
- **Stage** `extract_timeline` (mocked analyzer): a mix of past/future/recurring + one dateless +
  one below-threshold event → asserts the dateless and low-confidence ones are dropped and
  `replace_content_events` is called with the correctly-mapped content `Event`s (`event_type`,
  `occurred_at`=start, `details` with `source_quote`/`end_date`/`recurrence`).
- **Store** `replace_content_events` (sqlite `PostgresStore`): deletes only the document's
  `content` events, leaves `audit` events intact, inserts the new ones.
- **Robustness:** analyzer returns `None` → `replace_content_events` not called (prior events kept).
- **Prompt file** `prompts/analysis/timeline-extraction.md` exists and loads.
- Final gates: `ruff`, `mypy` strict, `pytest`, and `docker compose build api worker` (exit 0).

---

## 12. Phasing / out-of-scope (recap)

Phase 2 **writes** content events (past, future, and recurring rules with start + optional
`end_date` + RRULE in `details`). It does **not** expand recurrence, validate RRULEs, add an
agenda/reminders view, or backfill. Those are Phase 3 / later. Recurring events appear in the
meantime as their single rule event (`occurred_at` = start).

---

## 13. Open questions / future work

- **Reference date for relative dates:** Phase 2 drops relative expressions without an in-document
  reference. A future enhancement could pass the document's detected date as a fallback reference.
- **Confidence default:** `0.5` is a starting point; tune against a small evaluation set.
- **Custom prompt instructions:** the other analyzer steps support `config.generation.prompt_*`
  overrides; a `prompt_timeline` hook could be added later for consistency (omitted from v1).
- **Requirement IDs:** assign real FR/NFR IDs for the timeline content stream in `docs/requirements/`.
