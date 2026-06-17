# Timeline Phase 3 — Recurrence Expansion + Agenda — Design

- **Status:** Draft (approved in brainstorming, pending written-spec review)
- **Date:** 2026-06-17
- **Project:** `saga-core`
- **Builds on:** Timeline event log Phase 1 (event store + audit) + Phase 2 (content events,
  incl. the `RECURRING` event type with an RRULE in `details`). See
  [timeline event-log design](2026-06-16-timeline-event-log-design.md) (§3.6, §5, Phase 3).

---

## 1. Context & goal

Phase 2 persists a recurring obligation as a **single** `content` event: `event_type=RECURRING`,
`occurred_at` = the anchor/start date, and `details = {"source_quote": ..., "end_date"?: ISO,
"recurrence": "<RRULE>"}` (an RFC 5545 rule string the LLM emits, e.g.
`FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1`). The rule is stored once and **never** materialized.

Phase 3 makes recurrence useful at read time:

1. **On-read expansion** — expand each `RECURRING` rule into concrete occurrence events within a
   bounded, configurable horizon, as **inline synthetic events** in the result stream.
2. **Agenda surface** — a "what's coming up" view (`GET /agenda` + MCP `get_agenda`) that returns
   upcoming content events (future appointments/deadlines/dated facts) merged with expanded
   recurring occurrences, date-sorted ascending.
3. **Extraction-time RRULE validation** — a validator on the extraction schema so only valid
   RRULEs are persisted (the LLM self-corrects via saidex's validation-retry loop).

Reminders/notifications remain out of scope (a separate later concern); the schema already does
not preclude them.

### Decisions (from brainstorming)
- **Surface:** expansion lives in the read path (`TimelineService`) so general future views
  benefit, **plus** a thin agenda endpoint + MCP tool with agenda defaults.
- **RRULE engine:** `python-dateutil` (`dateutil.rrule.rrulestr`) — robust RFC 5545 support,
  already transitively in the lockfile; promote to a direct dependency.
- **Occurrence form:** inline synthetic `Event` objects (not persisted), deterministic synthetic
  id, `details.occurrence_of` back-reference; in an expanded window the bare rule is replaced by
  its occurrences.
- **Validator location:** implement in SAGA now (encapsulated `validate_rrule`), with a
  saidex-ready snippet delivered separately for later upstreaming (§5 + Appendix A); when saidex
  ships it, SAGA swaps the import in one line.

---

## 2. Goals & Non-Goals

### Goals
- A pure `expand_recurrences` unit + `TimelineService` integration (opt-in via `expand_recurrences`).
- `GET /agenda` + MCP `get_agenda` returning the unified, date-sorted upcoming stream.
- Extraction-time RRULE validation on `TimelineEvent.recurrence`.
- Config-driven horizon + per-rule occurrence cap.

### Non-Goals
- Materializing/persisting occurrences (rules stay the single source of truth).
- Reminders/notifications/UI surfaces (separate, later).
- Bumping saidex to 0.3.x or adding the validator to saidex in this work (the snippet is handed
  to the maintainer; SAGA carries a local validator meanwhile).
- Changing Phase 1/2 write paths (`append_event` / `replace_content_events` / audit stream).

---

## 3. Architecture & flow

Additive in the **read path** only; Phase 1/2 storage and write paths are untouched.

- **`expand_recurrences(rules, *, window_start, window_end, max_occurrences) -> list[Event]`** —
  pure function, new module `src/saga/events/recurrence.py`. No DB/LLM access → unit-testable in
  isolation. For each `RECURRING` rule it parses the RRULE and emits one synthetic `Event` per
  occurrence in the window (§4).
- **`TimelineService.query` (extended).** New `EventQuery.expand_recurrences: bool = False`
  (default false → existing `GET /timeline` behaviour unchanged). When true:
  1. Resolve the window: `window_end = q.occurred_to or now + horizon`,
     `window_start = q.occurred_from or now`.
  2. **Fetch the `RECURRING` rules separately** via the store with `event_types=[RECURRING]` and
     **no `occurred_from` lower bound** (a rule's anchor is often *before* the window while its
     occurrences fall *inside* it); the folder/category filters still apply.
  3. Expand the rules into synthetic occurrences via `expand_recurrences`.
  4. Run the normal windowed query for the **non-recurring** events (exclude `RECURRING`).
  5. Merge, sort by `occurred_at` ascending, drop the bare rule events. Apply the query's
     `limit`/`offset` to the merged result.
- **Agenda surface (thin):** `GET /agenda` (REST) + MCP `get_agenda` call `TimelineService.query`
  with agenda defaults: `expand_recurrences=True`, `categories=[content]`, window `now → now +
  horizon`, `order_by=occurred_at` ascending, optional `folder_id` (subtree). `GET /timeline`
  also gains an optional `expand` query param so general future views can expand too — the agenda
  is the convenience wrapper.

**What is deliberately NOT changed:** the event store schema, `append_event`,
`replace_content_events`, the audit stream, and the existing default (non-expanding) timeline
behaviour.

---

## 4. Expansion semantics

Per `RECURRING` rule (`expand_recurrences`):
- `dtstart = rule.occurred_at` (stored anchor, tz-aware UTC).
  `rule_obj = rrulestr(rule.details["recurrence"], dtstart=dtstart)`.
- Effective end: `eff_end = min(window_end, end_date)` when `details["end_date"]` is set, else
  `window_end` (the RRULE's own `UNTIL`/`COUNT` is honoured by dateutil regardless).
- Occurrences: `rule_obj.between(window_start, eff_end, inc=True)` → one synthetic `Event` per
  date.

**Synthetic occurrence event:** a normal `Event` with `event_type=RECURRING`, `category=content`,
same `document_id`/`summary`/`actor` as the rule, `occurred_at` = the occurrence datetime,
`recorded_at` = the rule's `recorded_at`, deterministic `event_id = f"{rule.event_id}@{date}"`
(ISO date; never collides with real ids, which contain no `@`), and
`details = {**rule.details, "occurrence_of": rule.event_id}`. Not persisted.

**Robustness (LLM-generated RRULEs):**
- **Invalid/missing RRULE:** `rrulestr` raises (or `details` has no `recurrence`) → that rule is
  **skipped**, a warning is logged (`saga.timeline`), and the query continues. One bad rule never
  aborts the whole read. (Belt-and-suspenders alongside §5's extraction validation, which keeps
  *new* data clean but cannot retroactively fix legacy/foreign-imported data.)
- **Safety cap:** a pathological pattern (e.g. `FREQ=DAILY` over a year, or `FREQ=HOURLY`) could
  flood the window. Each rule is capped at `max_occurrences_per_rule` (config, default 366);
  on overflow the list is truncated and a warning logged.

**Pass-through:** non-recurring future events (`APPOINTMENT`, future `DATED_FACT`) are already
concrete dates → returned unchanged, no expansion.

**Time zones:** tz-aware UTC throughout (anchor, `datetime.now(UTC)` window bounds, occurrences),
consistent with the existing event store.

---

## 5. Extraction-time RRULE validation

- **New `src/saga/llm/rrule.py`** with `validate_rrule(value: str) -> str`: parses via
  `dateutil.rrule.rrulestr(value)`; on failure raises `ValueError` with a clear, LLM-actionable
  message (e.g. *"Invalid RRULE '<v>': <reason>. Use an RFC 5545 RRULE such as
  'FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1'."*). A single function → trivially swappable for the
  saidex-provided validator later.
- **`TimelineEvent` (`src/saga/llm/schemas.py`)** gains a `@field_validator("recurrence")` that
  calls `validate_rrule` when the value is non-`None` (it stays optional/None for non-recurring
  events; the existing model-validator that clears `recurrence`/`end_date` for non-recurring kinds
  is unchanged and runs first/after as appropriate).
- **Self-correction:** SAGA extracts through saidex; saidex's `create_instance_safe` turns the
  `ValueError` into structured guidance fed back to the LLM, which retries (up to
  `max_primary_retries`). So invalid RRULEs are corrected at extraction time and only valid rules
  persist.
- **`python-dateutil`** becomes a **direct** dependency (`uv add python-dateutil`); it was only
  transitive before.

The reusable saidex form of this validator is in **Appendix A** — handed to the saidex maintainer
for upstreaming; SAGA adopts it later via a one-line import swap.

---

## 6. Agenda surface (REST + MCP)

- **`EventQuery.expand_recurrences: bool = False`** drives §3's service behaviour.
- **`GET /agenda`** (added to `src/saga/api/routes/timeline.py`, behind the existing auth):
  query params `from` (default `now`), `to` (default `now + horizon`), `folder_id` (optional,
  subtree), `limit`/`offset` (existing pagination defaults). Fixes `categories=[content]`,
  `expand_recurrences=True`, `order_by=occurred_at` ascending. Returns the existing
  `TimelineResponse` shape (synthetic occurrences inline).
- **`GET /timeline`** gains an optional `expand: bool = false` query param wired to
  `EventQuery.expand_recurrences`, so callers can expand any future-windowed timeline view.
- **MCP `get_agenda` tool** (`src/saga/mcp/`), description in `prompts/mcp/get_agenda.md` (no
  inlined prompt text), delegating to the same `TimelineService` path — symmetric with the
  existing `get_timeline` tool.

---

## 7. Config & errors

- **`TimelineConfig`** (`src/saga/core/config.py`, already exists) gains:
  - `recurrence_horizon_days: int = 366` — default agenda/expansion horizon.
  - `max_occurrences_per_rule: int = 366` — per-rule safety cap (§4).
  Surfaced in `config/config.yaml` with explanatory comments; no hard-coding.
- **Errors:** read-time invalid RRULE → `saga.timeline` warning + skip (no raise). Extraction-time
  invalid RRULE → `ValueError` (handled by saidex's retry loop). No new error types in
  `saga.core.errors` are required.

---

## 8. Testing

≥80% coverage on core packages; external services mocked.
- **`validate_rrule`:** valid rules pass; representative invalid strings raise `ValueError` with a
  helpful message; `None`/non-recurring path is a no-op (via the schema validator test).
- **`TimelineEvent` validator:** a valid `recurring` item validates; an invalid `recurrence`
  raises (so saidex would retry) — direct Pydantic construction, no LLM.
- **`expand_recurrences` (pure):** occurrence inside vs outside the horizon; `end_date` bound
  clamps; `UNTIL`/`COUNT` honoured; malformed RRULE skipped (warning, no raise); safety-cap
  truncation; deterministic synthetic ids + `occurrence_of`; pass-through of non-recurring events.
- **`TimelineService` integration:** a rule whose **anchor precedes** the window still expands
  into it; merge + ascending `occurred_at` sort; bare rule replaced by occurrences; default
  (`expand_recurrences=False`) behaviour unchanged.
- **`GET /agenda`** route test (window defaults, folder scope, pagination) + **`GET /timeline?expand=true`**;
  MCP `get_agenda` tool test.
- Final gates: `ruff`, `mypy` strict, `pytest`, `docker compose build api worker` (exit 0).

---

## 9. Phasing / notes

This is a single implementation plan (one cohesive subsystem: read-time expansion + agenda +
extraction validation). It depends only on the merged Phase 1/2 timeline — independent of the OKF
import branch.

**Open / future:**
- Upstream the RRULE validator into saidex (Appendix A), then swap SAGA's import; optionally bump
  saidex to 0.3.x (renamed entry function → call-site migration) as separate work.
- Reminders/agenda push surfaces remain a later, separate spec.

---

## Appendix A — saidex upstream candidate (RRULE validator)

Standalone, dependency-optional validator to add to saidex (e.g. `saidex/validators.py`). SAGA
mirrors this logic locally now and swaps to `from saidex.validators import validate_rrule` once
released.

```python
"""Optional RRULE (RFC 5545) validator. Requires python-dateutil."""
from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator


def validate_rrule(value: str) -> str:
    """Validate an RFC 5545 RRULE string; raise ValueError (→ LLM self-correction)."""
    try:
        from dateutil.rrule import rrulestr
    except ImportError as exc:  # keep dateutil optional for the core lib
        raise RuntimeError("RRULE validation requires 'python-dateutil'.") from exc
    try:
        rrulestr(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid RRULE '{value}': {exc}. "
            "Use an RFC 5545 RRULE such as 'FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1'."
        ) from exc
    return value


#: Annotated str type that self-validates as an RRULE when used in a schema field.
RRuleStr = Annotated[str, AfterValidator(validate_rrule)]
```
