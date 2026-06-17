# Timeline Phase 2 — Content Event Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract dated events / appointments / recurring obligations from document text with the LLM and persist them as `content` timeline events, so they flow into the timeline read path and the OKF export `log.md`.

**Architecture:** A new `extract_timeline` analyzer step (saidex JSON mode, externalised prompt) returns a `TimelineExtraction`; a new pipeline stage filters (dateless / low-confidence dropped), maps each item to a `content` `Event`, and atomically replaces the document's content events via `PostgresStore.replace_content_events`. Runs in the `analyzing` phase after `extract_values`. **No DB migration** — content events use the existing `events` table.

**Tech Stack:** Python 3.12, saidex, Pydantic v2, SQLAlchemy 2.0 async (asyncpg; aiosqlite in tests), ARQ, `uv`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-17-timeline-phase2-content-events-design.md`.

**Conventions (`saga-core/CLAUDE.md`):** fully typed, `uv run mypy` strict; `uv run ruff check . --fix` + `uv run ruff format .`; tests ≥80%; config over constants; prompts in `prompts/*.md`; structured extraction via saidex; errors from `saga.core.errors`; structured logging; English only. **No `Co-Authored-By` trailer in commits.** Commit locally only. All paths relative to `saga-core/`.

---

## Relevant existing code

- `src/saga/llm/schemas.py` — `ExtractedValueOut` / `ValueExtraction` show the lenient-schema pattern (`_LLM_MODEL_CONFIG`, `@model_validator(mode="before")` coercion, JSON-string unwrap).
- `src/saga/llm/analyzer.py` — `extract_values` (`:260-281`) shows the step pattern: `self._prompts.render(...)` + `self._extract(step=…, schema=…, system_prompt=…, text=self._truncate(content), mode=ExtractionMode.JSON, …)`.
- `src/saga/llm/prompts.py` — `render` substitutes only `{{ name }}` placeholders; literal single braces `{ }` (JSON examples) are left untouched. A prompt with no `{{ }}` placeholders renders with no kwargs.
- `src/saga/pipeline/stages.py` — `extract_values` stage (`:98-112`) shows the stage pattern.
- `src/saga/pipeline/tasks.py` — `extract_values` is invoked under `DocumentStatus.ANALYZING` inside a `tracer.step_span("extract_values", …) as callbacks` block (`:112-123`).
- `src/saga/storage/postgres.py` — `EventRow`, `_new_id`, `append_event`, `query_events`; `delete` and `EventCategory` are imported.
- `src/saga/core/models.py` — `Event`, `EventCategory` (`AUDIT`/`CONTENT`), `EventType` (`DATED_FACT`/`APPOINTMENT`/`RECURRING`).
- `src/saga/core/config.py` — `TimelineConfig`.
- Test harnesses: `tests/test_llm_schemas.py`, `tests/test_llm_analyzer.py` (`_patch(monkeypatch, {"<SchemaName>": instance})` recorder; `recorder.calls[i]["schema"|"mode"|"text"|"system_prompt"|"callbacks"]`; `_analyzer()`), `tests/test_pipeline_stages.py` (`_FakeAnalyzer` + `db: PostgresStore` sqlite fixture), `tests/storage/test_events_store.py` (`store` fixture + `_event(**kw)` helper), `tests/core/test_config_timeline.py`.

## File Structure

**New**
- `prompts/analysis/timeline-extraction.md` — the extraction prompt.

**Modify**
- `src/saga/core/config.py` + `config/config.yaml` — `TimelineConfig.content_min_confidence`.
- `src/saga/llm/schemas.py` — `TimelineEventOut` + `TimelineExtraction`.
- `src/saga/llm/analyzer.py` — `extract_timeline` method.
- `src/saga/storage/postgres.py` — `replace_content_events`.
- `src/saga/pipeline/stages.py` — `extract_timeline` stage + helpers.
- `src/saga/pipeline/tasks.py` — wire the stage.
- Tests: `tests/core/test_config_timeline.py`, `tests/test_llm_schemas.py`, `tests/test_llm_analyzer.py`, `tests/storage/test_events_store.py`, `tests/test_pipeline_stages.py`.

---

## Task 1: Config — `content_min_confidence`

**Files:** Modify `src/saga/core/config.py`, `config/config.yaml`; Test `tests/core/test_config_timeline.py`.

- [ ] **Step 1: Append the failing test** to `tests/core/test_config_timeline.py` (reuse its existing env-var fixture):

```python
def test_timeline_content_min_confidence_default() -> None:
    cfg = load_config()
    assert cfg.timeline.content_min_confidence == 0.5
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/core/test_config_timeline.py -k content_min_confidence -v --no-cov` → `AttributeError`.

- [ ] **Step 3: Add the field** to `TimelineConfig` in `src/saga/core/config.py`:

```python
    # Minimum LLM relevance/confidence for a content/timeline event to be kept.
    content_min_confidence: float = 0.5
```

- [ ] **Step 4: Add to `config/config.yaml`** under the existing `timeline:` section:

```yaml
  # Minimum confidence for an extracted content/timeline event to be kept.
  content_min_confidence: 0.5
```

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/core/test_config_timeline.py -v --no-cov`.

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/core/config.py config/config.yaml tests/core/test_config_timeline.py` and `uv run mypy src/saga/core/config.py`.

- [ ] **Step 7: Commit** — `git add src/saga/core/config.py config/config.yaml tests/core/test_config_timeline.py && git commit -m "feat: add TimelineConfig.content_min_confidence"`

---

## Task 2: Schema — `TimelineEventOut` + `TimelineExtraction`

**Files:** Modify `src/saga/llm/schemas.py`; Test `tests/test_llm_schemas.py`.

- [ ] **Step 1: Append the failing test** to `tests/test_llm_schemas.py`:

```python
from saga.llm.schemas import TimelineEventOut, TimelineExtraction


def test_timeline_extraction_parses_and_defaults() -> None:
    raw = {
        "events": [
            {"kind": "future", "description": "Policy expiry", "date": "2027-04-30",
             "confidence": 0.9}
        ]
    }
    ex = TimelineExtraction.model_validate(raw)
    assert ex.events[0].kind == "future"
    assert ex.events[0].end_date is None
    assert ex.events[0].recurrence is None
    assert ex.events[0].confidence == 0.9


def test_timeline_event_defaults_kind_past() -> None:
    ev = TimelineEventOut.model_validate({"description": "x", "date": "2026-01-01"})
    assert ev.kind == "past"
    assert ev.confidence == 1.0


def test_timeline_extraction_unwraps_json_string_events() -> None:
    ex = TimelineExtraction.model_validate({"events": '[{"description":"x","date":"2026-01-01"}]'})
    assert ex.events[0].date == "2026-01-01"
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/test_llm_schemas.py -k timeline -v --no-cov` → `ImportError`.

- [ ] **Step 3: Add the schemas** to `src/saga/llm/schemas.py` (after `ValueExtraction`):

```python
class TimelineEventOut(BaseModel):
    """A single dated event, appointment, or recurring obligation found in the text."""

    model_config = _LLM_MODEL_CONFIG

    kind: str = Field(
        default="past",
        description=(
            "One of: 'past' (something that happened on a date), 'future' (an upcoming "
            "appointment/deadline), or 'recurring' (a repeating obligation)."
        ),
    )
    description: str = Field(
        default="",
        description="A short human-readable description of the event, e.g. 'Policy concluded'.",
    )
    date: str = Field(
        default="",
        description=(
            "The event's date as ISO 'YYYY-MM-DD'. For a recurring event, the start/anchor "
            "date. Resolve relative expressions only against a reference date stated in the "
            "document; otherwise leave empty."
        ),
    )
    end_date: str | None = Field(
        default=None,
        description=(
            "Optional ISO 'YYYY-MM-DD' end date for a period or a recurrence that ends. "
            "Null when it is a single point in time or the end is open/unknown."
        ),
    )
    recurrence: str | None = Field(
        default=None,
        description=(
            "For 'recurring' events only: an RRULE pattern (RFC 5545), e.g. 'FREQ=YEARLY'. "
            "Describe only the repetition pattern; do not encode the end (use end_date). "
            "Null for non-recurring events."
        ),
    )
    source_quote: str = Field(
        default="",
        description="A short verbatim quote from the document supporting this event.",
    )
    confidence: float = Field(
        default=1.0,
        description="How confident/relevant this event is, from 0.0 to 1.0.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:  # noqa: ANN401 - tolerant of varied LLM shapes
        if not isinstance(data, dict):
            return data
        coerced = dict(data)
        for key in ("kind", "description", "date", "source_quote"):
            if coerced.get(key) is not None:
                coerced[key] = _stringify(coerced[key])
        for key in ("end_date", "recurrence"):
            value = coerced.get(key)
            if value is not None and value != "":
                coerced[key] = _stringify(value)
            elif value == "":
                coerced[key] = None
        return coerced


class TimelineExtraction(BaseModel):
    """All dated events extracted from a document (Phase 2 content stream)."""

    model_config = _LLM_MODEL_CONFIG

    events: list[TimelineEventOut] = Field(
        default_factory=list,
        description=(
            "Every dated event, appointment, deadline, or recurring obligation described in "
            "the document. Return an empty list if none are present. Do NOT restate raw "
            "identifiers or numbers (those are captured separately)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_events_list(cls, data: Any) -> Any:  # noqa: ANN401
        """Unwrap ``events`` when a model serialises the array as a JSON string."""
        if not isinstance(data, dict):
            return data
        raw = data.get("events")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return {**data, "events": parsed}
            except (ValueError, json.JSONDecodeError):
                pass
        return data
```

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/test_llm_schemas.py -k timeline -v --no-cov`.

- [ ] **Step 5: Lint/type** — `uv run ruff check src/saga/llm/schemas.py tests/test_llm_schemas.py` and `uv run mypy src/saga/llm/schemas.py`.

- [ ] **Step 6: Commit** — `git add src/saga/llm/schemas.py tests/test_llm_schemas.py && git commit -m "feat: add TimelineExtraction/TimelineEventOut LLM schema"`

---

## Task 3: Prompt + `analyzer.extract_timeline`

**Files:** Create `prompts/analysis/timeline-extraction.md`; Modify `src/saga/llm/analyzer.py`; Test `tests/test_llm_analyzer.py`.

- [ ] **Step 1: Append the failing test** to `tests/test_llm_analyzer.py` (mirror `test_extract_values_uses_json_mode` for the mode assertion; ensure `ExtractionMode` and the new schemas are imported at the top of the file):

```python
async def test_extract_timeline_returns_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(
        monkeypatch,
        {"TimelineExtraction": TimelineExtraction(
            events=[TimelineEventOut(kind="future", description="Policy expiry",
                                     date="2027-04-30", confidence=0.9)]
        )},
    )
    result = await _analyzer().extract_timeline(content="...expires 2027-04-30...")
    assert result is not None
    assert result.events[0].kind == "future"
    assert recorder.calls[0]["schema"] == "TimelineExtraction"


async def test_extract_timeline_uses_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _patch(monkeypatch, {"TimelineExtraction": TimelineExtraction()})
    await _analyzer().extract_timeline(content="x")
    assert recorder.calls[0]["mode"] == ExtractionMode.JSON
```

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/test_llm_analyzer.py -k extract_timeline -v --no-cov` → `AttributeError`/`ImportError`.

- [ ] **Step 3: Create `prompts/analysis/timeline-extraction.md`** (no `{{ }}` placeholders; single-brace JSON example is safe):

```markdown
You extract a document's **timeline**: the dated events, appointments, deadlines, and
recurring obligations described in its text.

Return a JSON object: { "events": [ ... ] }. Each event has:
- "kind": "past" (it happened), "future" (an upcoming appointment/deadline), or
  "recurring" (a repeating obligation).
- "description": a short phrase, e.g. "Policy concluded", "Payment due", "Annual renewal".
- "date": the event date as ISO "YYYY-MM-DD". For recurring events, the start/anchor date.
  Resolve relative dates ("in two weeks") only against a reference date stated in the
  document; if you cannot resolve an exact date, omit that event.
- "end_date": optional ISO end date for a period (e.g. a coverage term) or a recurrence
  that ends; null if it is a single point in time or the end is open/unknown.
- "recurrence": for "recurring" only, an RRULE pattern such as "FREQ=YEARLY" or
  "FREQ=MONTHLY;INTERVAL=3"; describe only the repetition, not the end. Null otherwise.
- "source_quote": a short verbatim quote supporting the event.
- "confidence": 0.0-1.0, how confident you are that this is a real, relevant dated event.

Only include events that are genuinely tied to a date. Do not restate raw identifiers,
numbers, or amounts — those are captured elsewhere. If the document has no dated events,
return { "events": [] }.
```

- [ ] **Step 4: Add the method** to `DocumentAnalyzer` in `src/saga/llm/analyzer.py` (add `TimelineExtraction` to the `from saga.llm.schemas import (...)` block; `ExtractionMode` is already imported):

```python
    async def extract_timeline(
        self, *, content: str, trace_callbacks: list[Any] | None = None
    ) -> TimelineExtraction | None:
        """Extract dated events / appointments / recurring obligations (FR-18, Phase 2).

        Uses JSON mode (more reliable for the nested list, like ``extract_values``).
        """
        system = self._prompts.render("analysis/timeline-extraction.md")
        return await self._extract(
            step="timeline_extraction",
            schema=TimelineExtraction,
            system_prompt=system,
            text=self._truncate(content),
            mode=ExtractionMode.JSON,
            trace_callbacks=trace_callbacks,
        )
```

- [ ] **Step 5: Run, expect pass** — `uv run pytest tests/test_llm_analyzer.py -k extract_timeline -v --no-cov`.

- [ ] **Step 6: Lint/type** — `uv run ruff check src/saga/llm tests/test_llm_analyzer.py prompts` and `uv run mypy src/saga/llm`.

- [ ] **Step 7: Commit** — `git add src/saga/llm/analyzer.py prompts/analysis/timeline-extraction.md tests/test_llm_analyzer.py && git commit -m "feat: add extract_timeline analyzer step + prompt"`

---

## Task 4: `PostgresStore.replace_content_events`

**Files:** Modify `src/saga/storage/postgres.py`; Test `tests/storage/test_events_store.py`.

- [ ] **Step 1: Append the failing test** to `tests/storage/test_events_store.py` (reuse the `store` fixture + `_event(**kw)` helper):

```python
async def test_replace_content_events_replaces_only_content(store) -> None:
    await store.append_event(
        _event(category=EventCategory.AUDIT, event_type=EventType.PLACEMENT, document_id="d1")
    )
    await store.replace_content_events(
        "d1",
        [_event(category=EventCategory.CONTENT, event_type=EventType.DATED_FACT,
                document_id="d1", summary="old")],
    )
    await store.replace_content_events(
        "d1",
        [_event(category=EventCategory.CONTENT, event_type=EventType.APPOINTMENT,
                document_id="d1", summary="new")],
    )
    content = await store.query_events(categories=[EventCategory.CONTENT], document_id="d1")
    assert [e.summary for e in content] == ["new"]  # prior content replaced
    audits = await store.query_events(categories=[EventCategory.AUDIT], document_id="d1")
    assert len(audits) == 1  # audit untouched


async def test_replace_content_events_empty_clears(store) -> None:
    await store.replace_content_events(
        "d2",
        [_event(category=EventCategory.CONTENT, event_type=EventType.DATED_FACT, document_id="d2")],
    )
    await store.replace_content_events("d2", [])
    assert await store.query_events(categories=[EventCategory.CONTENT], document_id="d2") == []
```

(`_event` defaults `event_id=""`; the store assigns an id. If the helper lacks a `summary`/`category` kwarg path, extend it minimally to pass them through.)

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/storage/test_events_store.py -k replace_content -v --no-cov` → `AttributeError`.

- [ ] **Step 3: Implement `replace_content_events`** on `PostgresStore` (after `query_events`). `Sequence` is available under `TYPE_CHECKING`; `delete`, `EventCategory`, `_new_id` are imported:

```python
    async def replace_content_events(self, document_id: str, events: Sequence[Event]) -> None:
        """Atomically replace a document's content events (delete + reinsert).

        Audit events are untouched. Content events are a derivable projection of the
        document, refreshed on each (re-)analysis.
        """
        async with self._sessions()() as session, session.begin():
            await session.execute(
                delete(EventRow).where(
                    EventRow.document_id == document_id,
                    EventRow.category == str(EventCategory.CONTENT),
                )
            )
            for event in events:
                session.add(
                    EventRow(
                        id=event.event_id or _new_id(),
                        category=str(event.category),
                        event_type=str(event.event_type),
                        document_id=event.document_id,
                        folder_id=event.folder_id,
                        occurred_at=event.occurred_at,
                        recorded_at=event.recorded_at,
                        actor=event.actor,
                        summary=event.summary,
                        confidence=event.confidence,
                        dedupe_key=event.dedupe_key,
                        details=dict(event.details),
                    )
                )
```

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/storage/test_events_store.py -v --no-cov`.

- [ ] **Step 5: Lint/type** — `uv run ruff check src/saga/storage/postgres.py tests/storage/test_events_store.py` and `uv run mypy src/saga/storage/postgres.py`.

- [ ] **Step 6: Commit** — `git add src/saga/storage/postgres.py tests/storage/test_events_store.py && git commit -m "feat: add PostgresStore.replace_content_events"`

---

## Task 5: Pipeline stage `extract_timeline`

**Files:** Modify `src/saga/pipeline/stages.py`; Test `tests/test_pipeline_stages.py`.

- [ ] **Step 1: Append the failing test** to `tests/test_pipeline_stages.py`. First extend `_FakeAnalyzer` to support timeline (mirror how it supports `extract_values` — add a `timeline` attribute set from `__init__`, and an async `extract_timeline(self, **kwargs)` that records the call and returns `self._timeline`). Then add:

```python
async def test_extract_timeline_persists_filtered_content_events(db: PostgresStore) -> None:
    analyzer = _FakeAnalyzer(
        timeline=TimelineExtraction(
            events=[
                TimelineEventOut(kind="future", description="Policy expiry",
                                 date="2027-04-30", confidence=0.9),
                TimelineEventOut(kind="past", description="No date", date="", confidence=0.9),
                TimelineEventOut(kind="past", description="Low conf", date="2026-01-01",
                                 confidence=0.1),
                TimelineEventOut(kind="recurring", description="Annual renewal",
                                 date="2026-05-01", end_date="2030-05-01",
                                 recurrence="FREQ=YEARLY", confidence=0.8),
            ]
        )
    )
    count = await extract_timeline(
        document_id="d1", markdown="text",
        db=db, analyzer=analyzer,  # type: ignore[arg-type]
        min_confidence=0.5,
    )
    assert count == 2  # dateless + low-confidence dropped
    events = await db.query_events(categories=[EventCategory.CONTENT], document_id="d1")
    types = {e.event_type for e in events}
    assert types == {EventType.APPOINTMENT, EventType.RECURRING}
    recurring = next(e for e in events if e.event_type == EventType.RECURRING)
    assert recurring.details["recurrence"] == "FREQ=YEARLY"
    assert recurring.details["end_date"] == "2030-05-01"
    assert recurring.summary == "Annual renewal"


async def test_extract_timeline_keeps_prior_events_on_failure(db: PostgresStore) -> None:
    await db.replace_content_events(
        "d9",
        [_make_content_event("d9")],  # see helper note below
    )
    analyzer = _FakeAnalyzer(timeline=None)
    count = await extract_timeline(
        document_id="d9", markdown="x",
        db=db, analyzer=analyzer,  # type: ignore[arg-type]
        min_confidence=0.5,
    )
    assert count == 0
    events = await db.query_events(categories=[EventCategory.CONTENT], document_id="d9")
    assert len(events) == 1  # prior kept, not wiped
```

For `_make_content_event`, build a minimal `content` `Event` inline (import `Event`, `EventCategory`, `EventType` from `saga.core.models`, `datetime`/`UTC`):

```python
def _make_content_event(document_id: str) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id="", category=EventCategory.CONTENT, event_type=EventType.DATED_FACT,
        document_id=document_id, occurred_at=now, recorded_at=now, actor="extraction",
        summary="prior",
    )
```

Add `extract_timeline`, `TimelineExtraction`, `TimelineEventOut`, `Event`/`EventCategory`/`EventType` to the test's imports.

- [ ] **Step 2: Run, expect failure** — `uv run pytest tests/test_pipeline_stages.py -k extract_timeline -v --no-cov` → `ImportError`.

- [ ] **Step 3: Implement the stage** in `src/saga/pipeline/stages.py`. Add runtime imports `from datetime import UTC, date, datetime` and extend the models import to runtime `from saga.core.models import Chunk, Event, EventCategory, EventType, ExtractedValue`. Then:

```python
_KIND_TO_EVENT_TYPE = {
    "past": EventType.DATED_FACT,
    "future": EventType.APPOINTMENT,
    "recurring": EventType.RECURRING,
}


def _parse_iso_date(value: str) -> datetime | None:
    """Parse an ISO ``YYYY-MM-DD`` (date-only) into a midnight-UTC datetime, or None."""
    text = value.strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


async def extract_timeline(
    *,
    document_id: str,
    markdown: str,
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    min_confidence: float,
    trace_callbacks: list[Any] | None = None,
) -> int:
    """Extract content/timeline events from the document text and persist them (Phase 2).

    Dateless events and events below *min_confidence* are dropped. On extractor failure the
    prior content events are kept (no replace). Returns the number of events persisted.
    """
    extraction = await analyzer.extract_timeline(content=markdown, trace_callbacks=trace_callbacks)
    if extraction is None:
        _log.warning("timeline_extraction_failed", document_id=document_id)
        return 0
    now = datetime.now(UTC)
    events: list[Event] = []
    for item in extraction.events:
        occurred = _parse_iso_date(item.date)
        if occurred is None or item.confidence < min_confidence:
            continue
        event_type = _KIND_TO_EVENT_TYPE.get(item.kind, EventType.DATED_FACT)
        details: dict[str, Any] = {"source_quote": item.source_quote}
        end = _parse_iso_date(item.end_date) if item.end_date else None
        if end is not None:
            details["end_date"] = end.date().isoformat()
        if event_type == EventType.RECURRING and item.recurrence:
            details["recurrence"] = item.recurrence
        events.append(
            Event(
                event_id="",
                category=EventCategory.CONTENT,
                event_type=event_type,
                document_id=document_id,
                folder_id=None,
                occurred_at=occurred,
                recorded_at=now,
                actor="extraction",
                summary=item.description,
                confidence=item.confidence,
                dedupe_key=None,
                details=details,
            )
        )
    await db.replace_content_events(document_id, events)
    _log.info("timeline_extracted", document_id=document_id, count=len(events))
    return len(events)
```

(`DocumentAnalyzer` stays under `TYPE_CHECKING`; the call is duck-typed. `Any` is already imported.)

- [ ] **Step 4: Run, expect pass** — `uv run pytest tests/test_pipeline_stages.py -k extract_timeline -v --no-cov` (then the whole file: `uv run pytest tests/test_pipeline_stages.py -v --no-cov`).

- [ ] **Step 5: Lint/type** — `uv run ruff check src/saga/pipeline/stages.py tests/test_pipeline_stages.py` and `uv run mypy src/saga/pipeline/stages.py`.

- [ ] **Step 6: Commit** — `git add src/saga/pipeline/stages.py tests/test_pipeline_stages.py && git commit -m "feat: add extract_timeline pipeline stage (filter + persist content events)"`

---

## Task 6: Wire the stage into the pipeline

**Files:** Modify `src/saga/pipeline/tasks.py`.

- [ ] **Step 1: Add `extract_timeline` to the stages import** in `tasks.py`:

```python
from saga.pipeline.stages import (
    classify_doc_type,
    compute_similarity,
    convert_to_markdown,
    extract_timeline,
    extract_values,
    index_chunks,
    place_in_folder,
    summarize,
)
```

- [ ] **Step 2: Add the stage block** in `ingest_document`, immediately AFTER the `extract_values` `tracer.step_span("extract_values", …)` block and BEFORE the `summarize` block (still under `DocumentStatus.ANALYZING`, no status change):

```python
        # ------------------------------------------------------------------
        # Stage 3b: extract content/timeline events (dates, appointments, recurring)
        # ------------------------------------------------------------------
        with tracer.step_span(
            "extract_timeline",
            input={"document_id": document_id},
        ) as callbacks:
            await extract_timeline(
                document_id=document_id,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                min_confidence=config.timeline.content_min_confidence,
                trace_callbacks=callbacks,
            )
```

- [ ] **Step 3: Type-check + regression** — `uv run mypy src/saga/pipeline` and `uv run pytest tests/test_pipeline_stages.py tests/test_pipeline_worker.py -v --no-cov` (existing pipeline tests still pass; the new stage is additive and its failure is non-fatal).

- [ ] **Step 4: Lint** — `uv run ruff check src/saga/pipeline/tasks.py`.

- [ ] **Step 5: Commit** — `git add src/saga/pipeline/tasks.py && git commit -m "feat: run extract_timeline in the ingestion pipeline"`

---

## Task 7: Full verification

**Files:** none.

- [ ] **Step 1:** `uv run ruff check . --fix && uv run ruff format .` → clean.
- [ ] **Step 2:** `uv run mypy` → `Success` (full run incl. tests).
- [ ] **Step 3:** `uv run pytest` → all pass, coverage ≥ 80%.
- [ ] **Step 4:** Mandatory Docker build (from workspace root `d:\Projekte\Archiv`): `docker compose build api worker` → exit 0.

---

## Self-Review notes (already applied)

- **Spec coverage:** §3 schema → Task 2; §4 analyzer step + prompt → Task 3; §5 filtering → Task 5; §6 persistence (`replace_content_events`, no migration, field mapping incl. `details.end_date`/`recurrence`, `folder_id=None`) → Tasks 4/5; §8 pipeline wiring after `extract_values` under ANALYZING → Task 6; §9 config → Task 1; §10 robustness (None→keep / empty→clear, non-fatal) → Tasks 4/5/6; §11 testing → each task + Task 7. Out-of-scope (recurrence expansion, RRULE validation, agenda, backfill) correctly absent.
- **Type consistency:** `TimelineExtraction.events: list[TimelineEventOut]`; `analyzer.extract_timeline(content=…) -> TimelineExtraction | None`; `extract_timeline(*, document_id, markdown, db, analyzer, min_confidence, trace_callbacks=None) -> int`; `replace_content_events(document_id, events: Sequence[Event])`; `EventType.DATED_FACT/APPOINTMENT/RECURRING`; `EventCategory.CONTENT` — all consistent across tasks.
- **Sharp edges for the implementer:** (a) extend `_FakeAnalyzer` in `tests/test_pipeline_stages.py` to support `extract_timeline` (mirror `extract_values`); (b) ensure `ExtractionMode` + the new schemas are imported in `tests/test_llm_analyzer.py`; (c) the timeline prompt must contain no `{{ }}` placeholders (single-brace JSON example is fine).

## Commit note

No `Co-Authored-By` trailer (per the user's standing preference / CLAUDE.md). Commit locally; do not push unless asked.
