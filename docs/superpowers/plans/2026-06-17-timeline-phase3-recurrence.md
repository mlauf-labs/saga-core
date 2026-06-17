# Timeline Phase 3 — Recurrence Expansion + Agenda Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand stored `RECURRING` timeline rules into concrete occurrences on read (within a bounded horizon), expose an agenda surface, and validate RRULE strings at extraction time so only valid rules persist.

**Architecture:** A pure `expand_recurrences` helper (python-dateutil) turns `RECURRING` rule events into inline synthetic occurrence events. `TimelineService.query` opts in via `EventQuery.expand_recurrences`: it fetches the rules separately (no lower time bound — the anchor often precedes the window), expands them, fetches the non-recurring events in the window, merges and date-sorts. A thin `GET /agenda` + MCP `get_agenda` wrap this with agenda defaults. A `validate_rrule` helper + a Pydantic field-validator on the extraction schema make the LLM self-correct invalid RRULEs through saidex's validation-retry loop.

**Tech Stack:** Python 3.12, `python-dateutil` (`dateutil.rrule.rrulestr`), Pydantic v2 (`field_validator`), FastAPI, ARQ, SQLAlchemy async (sqlite+aiosqlite in tests), saidex (extraction), pytest + pytest-asyncio (`asyncio_mode=auto`), uv.

**Baseline branch:** Branch from `develop` (has the merged Phase 1/2 timeline). Phase 3 is independent of the OKF-import branch. Do **not** run HEAD-changing git commands; after each commit verify `git rev-parse --abbrev-ref HEAD` is the feature branch.

**Commit convention:** Conventional Commits, imperative mood, English. **Do not add a `Co-Authored-By: Claude` trailer.**

**Design spec:** `docs/superpowers/specs/2026-06-17-timeline-phase3-recurrence-design.md`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/saga/llm/rrule.py` | RRULE validation helper | Create `validate_rrule`. |
| `src/saga/llm/schemas.py` | LLM extraction schemas | Add a `field_validator("recurrence")` to `TimelineEventOut`. |
| `src/saga/events/recurrence.py` | Recurrence expansion | Create `expand_recurrences` (pure). |
| `src/saga/core/config.py` | Config | Add `recurrence_horizon_days` + `max_occurrences_per_rule` to `TimelineConfig`. |
| `config/config.yaml` | Config values | Add the two timeline keys (operator visibility). |
| `src/saga/events/service.py` | Read path | `EventQuery.expand_recurrences`; `TimelineService` config injection + expansion path + `_fetch_all`. |
| `src/saga/api/app.py` | Wiring | Construct `TimelineService` with the config values. |
| `src/saga/api/routes/timeline.py` | REST | `expand` param on `/timeline`; new `GET /agenda`. |
| `src/saga/mcp/server.py` | MCP | New `get_agenda` tool + registration. |
| `prompts/mcp/get_agenda.md` | MCP tool description | Create. |
| `pyproject.toml` | Deps | Add `python-dateutil` as a direct dependency. |

Tests: `tests/test_llm_rrule.py`, `tests/test_timeline_schema_validation.py`, `tests/events/test_recurrence.py`, `tests/events/test_timeline_service_expand.py`, `tests/test_api_agenda.py`, `tests/test_mcp_get_agenda.py`.

---

## Task 1: `validate_rrule` helper + `python-dateutil` dependency

**Files:**
- Create: `src/saga/llm/rrule.py`
- Modify: `pyproject.toml`
- Test: `tests/test_llm_rrule.py`

- [ ] **Step 1: Add `python-dateutil` as a direct dependency**

Run: `uv add python-dateutil`
Expected: `pyproject.toml` gains `"python-dateutil>=2.9"` in `dependencies`; `uv.lock` updates (dateutil was already transitive, so this is a fast no-network-build change). Verify with `uv run python -c "import dateutil.rrule; print('ok')"` → prints `ok`.

- [ ] **Step 2: Write the failing test**

Create `tests/test_llm_rrule.py`:

```python
from __future__ import annotations

import pytest

from saga.llm.rrule import validate_rrule


def test_validate_rrule_accepts_valid_rule() -> None:
    value = "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1"
    assert validate_rrule(value) == value


def test_validate_rrule_accepts_simple_frequency() -> None:
    assert validate_rrule("FREQ=MONTHLY") == "FREQ=MONTHLY"


def test_validate_rrule_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="Invalid RRULE"):
        validate_rrule("not-an-rrule")


def test_validate_rrule_rejects_unknown_freq() -> None:
    with pytest.raises(ValueError, match="Invalid RRULE"):
        validate_rrule("FREQ=NOPE")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_rrule.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'saga.llm.rrule'`.

- [ ] **Step 4: Implement `validate_rrule`**

Create `src/saga/llm/rrule.py`:

```python
"""RRULE (RFC 5545) validation for LLM-extracted recurrence patterns.

Encapsulated in one function so it can later be replaced by a saidex-provided
validator (see the Phase 3 design, Appendix A) with a one-line import swap.
"""

from __future__ import annotations

from dateutil.rrule import rrulestr


def validate_rrule(value: str) -> str:
    """Return *value* if it is a valid RFC 5545 RRULE, else raise ``ValueError``.

    The error message is LLM-actionable: saidex feeds it back to the model so it
    can self-correct an invalid pattern during extraction.
    """
    try:
        rrulestr(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid RRULE '{value}': {exc}. "
            "Use an RFC 5545 RRULE such as 'FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1'."
        ) from exc
    return value
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_rrule.py -v -p no:cacheprovider --no-cov`
Expected: PASS (4 passed).

- [ ] **Step 6: Self-check + commit**

Run: `uv run ruff check src/saga/llm/rrule.py tests/test_llm_rrule.py --fix && uv run mypy src/saga/llm/rrule.py`

```bash
git add pyproject.toml uv.lock src/saga/llm/rrule.py tests/test_llm_rrule.py
git commit -m "feat: add validate_rrule helper and python-dateutil dependency"
```

---

## Task 2: `field_validator` on `TimelineEventOut.recurrence`

**Files:**
- Modify: `src/saga/llm/schemas.py`
- Test: `tests/test_timeline_schema_validation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_timeline_schema_validation.py`:

```python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from saga.llm.schemas import TimelineEventOut


def test_valid_recurring_event_validates() -> None:
    ev = TimelineEventOut(
        kind="recurring",
        description="Annual premium",
        date="2026-05-01",
        recurrence="FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1",
        source_quote="every year on 1 May",
    )
    assert ev.recurrence == "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1"


def test_none_recurrence_is_allowed() -> None:
    ev = TimelineEventOut(kind="past", description="Signed", date="2026-05-01")
    assert ev.recurrence is None


def test_empty_recurrence_coerced_to_none() -> None:
    ev = TimelineEventOut(kind="past", description="Signed", date="2026-05-01", recurrence="")
    assert ev.recurrence is None


def test_invalid_recurrence_raises() -> None:
    with pytest.raises(ValidationError, match="Invalid RRULE"):
        TimelineEventOut(
            kind="recurring",
            description="bad",
            date="2026-05-01",
            recurrence="FREQ=NONSENSE",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_timeline_schema_validation.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `test_invalid_recurrence_raises` fails (no validation yet; the invalid value is accepted).

- [ ] **Step 3: Add the field validator**

In `src/saga/llm/schemas.py`: ensure `field_validator` is imported from pydantic (the module already imports `model_validator`; add `field_validator` to that import). Add `from saga.llm.rrule import validate_rrule` to the imports. Then add this method to the `TimelineEventOut` class (after the existing `_coerce` model-validator):

```python
    @field_validator("recurrence")
    @classmethod
    def _validate_recurrence(cls, value: str | None) -> str | None:
        """Reject invalid RRULEs so saidex makes the LLM self-correct (Phase 3)."""
        if value is None or value == "":
            return value
        return validate_rrule(value)
```

NOTE: the `_coerce` `model_validator(mode="before")` already turns `""` into `None` before field validators run, so in practice `value` is `None` or a real string here; the explicit `""` guard is belt-and-suspenders. Clearing `recurrence` for non-recurring events happens later in the pipeline stage (`extract_timeline`), not in this schema — this validator only checks syntactic RRULE validity when a value is present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_timeline_schema_validation.py -v -p no:cacheprovider --no-cov`
Expected: PASS (4 passed).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/llm/schemas.py tests/test_timeline_schema_validation.py --fix && uv run mypy src/saga/llm/schemas.py`

```bash
git add src/saga/llm/schemas.py tests/test_timeline_schema_validation.py
git commit -m "feat: validate RRULE on TimelineEventOut.recurrence"
```

---

## Task 3: `expand_recurrences` pure function

**Files:**
- Create: `src/saga/events/recurrence.py`
- Test: `tests/events/test_recurrence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_recurrence.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType
from saga.events.recurrence import expand_recurrences


def _rule(recurrence: str | None, *, anchor: str = "2026-05-01", end_date: str | None = None) -> Event:
    details: dict[str, object] = {"source_quote": "q"}
    if recurrence is not None:
        details["recurrence"] = recurrence
    if end_date is not None:
        details["end_date"] = end_date
    return Event(
        event_id="r1",
        category=EventCategory.CONTENT,
        event_type=EventType.RECURRING,
        document_id="d1",
        occurred_at=datetime.fromisoformat(anchor).replace(tzinfo=UTC),
        recorded_at=datetime(2026, 5, 10, tzinfo=UTC),
        actor="llm",
        summary="Annual premium",
        details=details,
    )


def test_expands_yearly_rule_within_window() -> None:
    rule = _rule("FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1")
    occ = expand_recurrences(
        [rule],
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2028, 12, 31, tzinfo=UTC),
        max_occurrences=366,
    )
    dates = sorted(e.occurred_at.date().isoformat() for e in occ)
    assert dates == ["2026-05-01", "2027-05-01", "2028-05-01"]
    first = next(e for e in occ if e.occurred_at.date().isoformat() == "2027-05-01")
    assert first.event_id == "r1@2027-05-01"
    assert first.event_type == EventType.RECURRING
    assert first.details["occurrence_of"] == "r1"
    assert first.document_id == "d1" and first.summary == "Annual premium"


def test_window_excludes_outside_occurrences() -> None:
    rule = _rule("FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1")
    occ = expand_recurrences(
        [rule],
        window_start=datetime(2027, 1, 1, tzinfo=UTC),
        window_end=datetime(2027, 12, 31, tzinfo=UTC),
        max_occurrences=366,
    )
    assert [e.occurred_at.date().isoformat() for e in occ] == ["2027-05-01"]


def test_end_date_bounds_expansion() -> None:
    rule = _rule("FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1", end_date="2027-12-31")
    occ = expand_recurrences(
        [rule],
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2030, 12, 31, tzinfo=UTC),
        max_occurrences=366,
    )
    assert [e.occurred_at.date().isoformat() for e in occ] == ["2026-05-01", "2027-05-01"]


def test_invalid_rule_is_skipped() -> None:
    bad = _rule("FREQ=NONSENSE")
    missing = _rule(None)
    occ = expand_recurrences(
        [bad, missing],
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2028, 1, 1, tzinfo=UTC),
        max_occurrences=366,
    )
    assert occ == []


def test_safety_cap_truncates() -> None:
    rule = _rule("FREQ=DAILY")
    occ = expand_recurrences(
        [rule],
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2030, 1, 1, tzinfo=UTC),
        max_occurrences=10,
    )
    assert len(occ) == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_recurrence.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'saga.events.recurrence'`.

- [ ] **Step 3: Implement `expand_recurrences`**

Create `src/saga/events/recurrence.py`:

```python
"""On-read expansion of RECURRING timeline rules into concrete occurrences.

Pure (no DB/LLM access). The TimelineService calls this when a query opts into
recurrence expansion. Occurrences are synthetic, never persisted. See the Phase 3
design (§4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from dateutil.rrule import rrulestr

from saga.core.logging import get_logger
from saga.core.models import Event, EventType

_log = get_logger("saga.timeline")


def _parse_end(value: object) -> datetime | None:
    """Parse an ISO date ``details['end_date']`` into an inclusive end-of-day UTC bound."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(hour=23, minute=59, second=59, tzinfo=UTC)


def _occurrence(rule: Event, occurred_at: datetime) -> Event:
    return Event(
        event_id=f"{rule.event_id}@{occurred_at.date().isoformat()}",
        category=rule.category,
        event_type=EventType.RECURRING,
        document_id=rule.document_id,
        folder_id=rule.folder_id,
        occurred_at=occurred_at,
        recorded_at=rule.recorded_at,
        actor=rule.actor,
        summary=rule.summary,
        confidence=rule.confidence,
        dedupe_key=None,
        details={**rule.details, "occurrence_of": rule.event_id},
    )


def expand_recurrences(
    rules: list[Event],
    *,
    window_start: datetime,
    window_end: datetime,
    max_occurrences: int,
) -> list[Event]:
    """Expand each RECURRING *rule* into synthetic occurrence events within the window.

    Invalid/missing RRULEs are skipped (logged, never raised) so one bad rule cannot
    abort a read. Each rule is capped at *max_occurrences* to guard against pathological
    patterns.
    """
    out: list[Event] = []
    for rule in rules:
        rrule_str = rule.details.get("recurrence")
        if not isinstance(rrule_str, str) or not rrule_str or rule.occurred_at is None:
            _log.warning("recurrence_unexpandable", event_id=rule.event_id)
            continue
        try:
            rset = rrulestr(rrule_str, dtstart=rule.occurred_at)
        except (ValueError, TypeError) as exc:
            _log.warning("recurrence_invalid_rule", event_id=rule.event_id, error=str(exc))
            continue
        end = _parse_end(rule.details.get("end_date"))
        effective_end = min(window_end, end) if end is not None else window_end
        try:
            dates = rset.between(window_start, effective_end, inc=True)
        except (ValueError, TypeError) as exc:
            _log.warning("recurrence_expand_failed", event_id=rule.event_id, error=str(exc))
            continue
        if len(dates) > max_occurrences:
            _log.warning(
                "recurrence_capped",
                event_id=rule.event_id,
                count=len(dates),
                cap=max_occurrences,
            )
            dates = dates[:max_occurrences]
        out.extend(_occurrence(rule, occ) for occ in dates)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/events/test_recurrence.py -v -p no:cacheprovider --no-cov`
Expected: PASS (5 passed).

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/events/recurrence.py tests/events/test_recurrence.py --fix && uv run mypy src/saga/events/recurrence.py`

```bash
git add src/saga/events/recurrence.py tests/events/test_recurrence.py
git commit -m "feat: add expand_recurrences (on-read RRULE expansion)"
```

---

## Task 4: `TimelineConfig` fields + `config.yaml`

**Files:**
- Modify: `src/saga/core/config.py`, `config/config.yaml`
- Test: `tests/test_config_timeline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_timeline.py`:

```python
from __future__ import annotations

from saga.core.config import TimelineConfig


def test_timeline_config_recurrence_defaults() -> None:
    cfg = TimelineConfig()
    assert cfg.recurrence_horizon_days == 366
    assert cfg.max_occurrences_per_rule == 366


def test_timeline_config_overrides() -> None:
    cfg = TimelineConfig(recurrence_horizon_days=90, max_occurrences_per_rule=50)
    assert cfg.recurrence_horizon_days == 90
    assert cfg.max_occurrences_per_rule == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_timeline.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `TimelineConfig` has no `recurrence_horizon_days` (validation/attribute error).

- [ ] **Step 3: Add the fields**

In `src/saga/core/config.py`, the `TimelineConfig` class currently ends with `max_page_size: int = 500`. Append two fields:

```python
    # Default horizon (in days) for recurrence expansion and the agenda view.
    recurrence_horizon_days: int = 366
    # Per-rule safety cap on expanded occurrences (guards against pathological RRULEs).
    max_occurrences_per_rule: int = 366
```

- [ ] **Step 4: Reflect the keys in `config/config.yaml`**

Find the `timeline:` section in `config/config.yaml` (it already has `rationale_top_n` / `default_page_size` / `max_page_size`). Add, with the same indentation:

```yaml
  # Default horizon (days) for recurrence expansion / the agenda view.
  recurrence_horizon_days: 366
  # Per-rule cap on expanded occurrences (guards against pathological RRULEs).
  max_occurrences_per_rule: 366
```

If there is no `timeline:` section, add one mirroring the field names under the top-level config mapping. (Defaults apply even if absent — this is for operator visibility.)

- [ ] **Step 5: Run test + config-load smoke check**

Run: `uv run pytest tests/test_config_timeline.py -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed).
Run: `uv run python -c "from saga.core.config import load_config; c=load_config(); print(c.timeline.recurrence_horizon_days, c.timeline.max_occurrences_per_rule)"`
Expected: prints `366 366` (or your configured values) without error.

- [ ] **Step 6: Self-check + commit**

Run: `uv run ruff check src/saga/core/config.py tests/test_config_timeline.py --fix && uv run mypy src/saga/core/config.py`

```bash
git add src/saga/core/config.py config/config.yaml tests/test_config_timeline.py
git commit -m "feat: add recurrence horizon + occurrence cap to TimelineConfig"
```

---

## Task 5: `EventQuery.expand_recurrences` + `TimelineService` expansion path

**Files:**
- Modify: `src/saga/events/service.py`, `src/saga/api/app.py`
- Test: `tests/events/test_timeline_service_expand.py`

- [ ] **Step 1: Write the failing test**

Create `tests/events/test_timeline_service_expand.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService
from saga.storage.postgres import PostgresStore


@pytest_asyncio.fixture
async def store() -> PostgresStore:
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    return s


async def _seed_rule(store: PostgresStore) -> None:
    # Anchor in the PAST (2024) — the rule row's occurred_at precedes the agenda window,
    # but its yearly occurrences fall inside it. Expansion must still find them.
    await store.append_event(
        Event(
            event_id="rule-1",
            category=EventCategory.CONTENT,
            event_type=EventType.RECURRING,
            document_id="d1",
            occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
            recorded_at=datetime(2024, 5, 10, tzinfo=UTC),
            actor="llm",
            summary="Annual premium",
            details={"source_quote": "q", "recurrence": "FREQ=YEARLY;BYMONTH=5;BYMONTHDAY=1"},
        )
    )


async def test_expand_includes_occurrences_from_past_anchor(store: PostgresStore) -> None:
    await _seed_rule(store)
    service = TimelineService(store, recurrence_horizon_days=366, max_occurrences_per_rule=366)
    query = EventQuery(
        categories=(EventCategory.CONTENT,),
        occurred_from=datetime(2027, 1, 1, tzinfo=UTC),
        occurred_to=datetime(2027, 12, 31, tzinfo=UTC),
        order_by="occurred_at",
        descending=False,
        expand_recurrences=True,
        limit=100,
    )
    events = await service.query(query)
    # The bare rule (anchor 2024) is replaced by its 2027 occurrence.
    assert [e.event_id for e in events] == ["rule-1@2027-05-01"]
    assert events[0].details["occurrence_of"] == "rule-1"


async def test_expand_false_returns_bare_rule(store: PostgresStore) -> None:
    await _seed_rule(store)
    service = TimelineService(store)
    query = EventQuery(categories=(EventCategory.CONTENT,), limit=100)
    events = await service.query(query)
    assert [e.event_id for e in events] == ["rule-1"]  # unchanged default behaviour
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_timeline_service_expand.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `EventQuery.__init__() got an unexpected keyword argument 'expand_recurrences'`.

- [ ] **Step 3: Add the `expand_recurrences` field to `EventQuery`**

In `src/saga/events/service.py`, the `EventQuery` dataclass ends with `offset: int = 0`. Add:

```python
    expand_recurrences: bool = False
```

- [ ] **Step 4: Add runtime imports + the expansion path to `TimelineService`**

The top of `service.py` imports `datetime` under `TYPE_CHECKING` and `Event`/`EventCategory`/`EventType` under `TYPE_CHECKING`. The expansion path needs `datetime`/`UTC`/`timedelta`, `Event`, and `EventType` at **runtime**. Update the imports so the runtime block includes:

```python
from datetime import UTC, datetime, timedelta

from saga.events.recurrence import expand_recurrences
from saga.storage.postgres import descendant_ids
from saga.core.models import Event, EventType
```

(Keep `EventCategory` wherever it is used; if only used for typing it may stay under `TYPE_CHECKING`. `Literal`/`Protocol`/`Sequence` imports are unchanged.) Add a module constant near the top:

```python
_RULE_PAGE = 500
```

Then replace the `TimelineService` class body with config injection, a shared scope resolver, the expansion path, and a paging helper:

```python
class TimelineService:
    """Resolves folder subtrees and delegates to the store's ``query_events``."""

    def __init__(
        self,
        store: TimelineStore,
        *,
        recurrence_horizon_days: int = 366,
        max_occurrences_per_rule: int = 366,
    ) -> None:
        self._store = store
        self._horizon_days = recurrence_horizon_days
        self._max_occurrences = max_occurrences_per_rule

    async def _resolve_scope(
        self, q: EventQuery
    ) -> tuple[list[str] | None, list[str] | None]:
        if q.folder_id is None:
            return None, None
        if q.include_subtree:
            parents = await self._store.parents_map()
            folder_ids = descendant_ids(q.folder_id, parents)
        else:
            folder_ids = [q.folder_id]
        document_ids = await self._store.document_ids_in_folders(folder_ids)
        return folder_ids, document_ids

    async def query(self, q: EventQuery) -> list[Event]:
        folder_ids, document_ids = await self._resolve_scope(q)
        if q.expand_recurrences:
            return await self._query_expanded(q, folder_ids, document_ids)
        return await self._store.query_events(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=q.occurred_from,
            occurred_to=q.occurred_to,
            order_by=q.order_by,
            descending=q.descending,
            limit=q.limit,
            offset=q.offset,
        )

    async def _query_expanded(
        self,
        q: EventQuery,
        folder_ids: list[str] | None,
        document_ids: list[str] | None,
    ) -> list[Event]:
        now = datetime.now(UTC)
        window_start = q.occurred_from or now
        window_end = q.occurred_to or (now + timedelta(days=self._horizon_days))
        # Recurring rules: fetch ALL in scope (no lower time bound — a rule's anchor
        # often precedes the window while its occurrences fall inside it).
        rules = await self._fetch_all(
            categories=q.categories,
            event_types=(EventType.RECURRING,),
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=None,
            occurred_to=None,
        )
        occurrences = expand_recurrences(
            rules,
            window_start=window_start,
            window_end=window_end,
            max_occurrences=self._max_occurrences,
        )
        # Non-recurring events within the window (drop any bare RECURRING rows).
        others = await self._fetch_all(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            document_ids=document_ids,
            occurred_from=window_start,
            occurred_to=window_end,
        )
        others = [e for e in others if e.event_type != EventType.RECURRING]
        merged = sorted(
            [*others, *occurrences],
            key=lambda e: e.occurred_at or e.recorded_at,
            reverse=q.descending,
        )
        end = q.offset + q.limit if q.limit else None
        return merged[q.offset : end]

    async def _fetch_all(
        self,
        *,
        categories: tuple[EventCategory, ...] | None,
        event_types: tuple[EventType, ...] | None,
        document_id: str | None,
        folder_ids: list[str] | None,
        document_ids: list[str] | None,
        occurred_from: datetime | None,
        occurred_to: datetime | None,
    ) -> list[Event]:
        """Page query_events (occurred_at asc) until a short page; return everything."""
        out: list[Event] = []
        offset = 0
        while True:
            page = await self._store.query_events(
                categories=categories,
                event_types=event_types,
                document_id=document_id,
                folder_ids=folder_ids,
                document_ids=document_ids,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                order_by="occurred_at",
                descending=False,
                limit=_RULE_PAGE,
                offset=offset,
            )
            out.extend(page)
            if len(page) < _RULE_PAGE:
                return out
            offset += _RULE_PAGE
```

NOTE: `EventCategory` is referenced in the `_fetch_all` signature annotation; with `from __future__ import annotations` it is a string annotation, so importing it under `TYPE_CHECKING` is sufficient — keep it wherever it already is. Only `Event`, `EventType`, the `datetime` trio, `descendant_ids`, and `expand_recurrences` must be runtime imports.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/events/test_timeline_service_expand.py -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed). Then run the existing timeline tests to confirm no regression in the default path: `uv run pytest tests/test_api_timeline.py tests/test_mcp_get_timeline.py -p no:cacheprovider --no-cov` → all pass.

- [ ] **Step 6: Inject config when constructing the service**

In `src/saga/api/app.py`, line ~104 currently reads `timeline_service = TimelineService(db)`. Replace with:

```python
        timeline_service = TimelineService(
            db,
            recurrence_horizon_days=cfg.timeline.recurrence_horizon_days,
            max_occurrences_per_rule=cfg.timeline.max_occurrences_per_rule,
        )
```

- [ ] **Step 7: Self-check + commit**

Run: `uv run ruff check src/saga/events/service.py src/saga/api/app.py tests/events/test_timeline_service_expand.py --fix && uv run mypy src/saga/events/service.py src/saga/api/app.py`

```bash
git add src/saga/events/service.py src/saga/api/app.py tests/events/test_timeline_service_expand.py
git commit -m "feat: expand recurring rules on read in TimelineService"
```

---

## Task 6: REST — `GET /agenda` + `expand` param on `/timeline`

**Files:**
- Modify: `src/saga/api/routes/timeline.py`
- Test: `tests/test_api_agenda.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_agenda.py`:

```python
"""API tests for GET /agenda (expanded upcoming events)."""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from saga.api.app import create_app
from saga.api.dependencies import Services
from saga.core.config import AppConfig
from saga.core.models import Event, EventCategory, EventType
from saga.events import EventRecorder, TimelineService
from saga.storage.postgres import PostgresStore
from tests.conftest import InMemoryBinaryStore

TEST_TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAGA_API_TOKENS", TEST_TOKEN)
    monkeypatch.setenv("OPENSEARCH_PASSWORD", "pw")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ak")
    monkeypatch.setenv("MINIO_SECRET_KEY", "sk")


@pytest_asyncio.fixture
async def store() -> AsyncIterator[PostgresStore]:
    tmp = Path(tempfile.mkdtemp()) / "agenda.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}", poolclass=NullPool)
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    try:
        yield s
    finally:
        await s.close()
        tmp.unlink(missing_ok=True)


@pytest.fixture
def config() -> AppConfig:
    cfg = AppConfig()
    cfg.security.bearer_tokens = TEST_TOKEN
    return cfg


@pytest.fixture
def services(store: PostgresStore, config: AppConfig) -> Services:
    return Services(
        config=config,
        db=store,
        opensearch=None,  # type: ignore[arg-type]
        minio=InMemoryBinaryStore(),
        queue=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(store),
        timeline=TimelineService(
            store,
            recurrence_horizon_days=config.timeline.recurrence_horizon_days,
            max_occurrences_per_rule=config.timeline.max_occurrences_per_rule,
        ),
    )


async def _seed(store: PostgresStore) -> None:
    now = datetime.now(UTC)
    # A yearly rule anchored last year → occurrences upcoming.
    await store.append_event(
        Event(
            event_id="rule-1",
            category=EventCategory.CONTENT,
            event_type=EventType.RECURRING,
            document_id="d1",
            occurred_at=now - timedelta(days=400),
            recorded_at=now - timedelta(days=400),
            actor="llm",
            summary="Annual premium",
            details={"source_quote": "q", "recurrence": "FREQ=YEARLY"},
        )
    )
    # A one-off upcoming appointment.
    await store.append_event(
        Event(
            event_id="appt-1",
            category=EventCategory.CONTENT,
            event_type=EventType.APPOINTMENT,
            document_id="d1",
            occurred_at=now + timedelta(days=30),
            recorded_at=now,
            actor="llm",
            summary="Dentist",
        )
    )


async def test_agenda_returns_upcoming_expanded(store: PostgresStore, services: Services) -> None:
    await _seed(store)
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        resp = client.get("/agenda", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    ids = [item["event_id"] for item in body["items"]]
    # The recurring rule is expanded (synthetic id with '@') and the appointment is present;
    # the bare rule id 'rule-1' is NOT returned.
    assert "appt-1" in ids
    assert any(i.startswith("rule-1@") for i in ids)
    assert "rule-1" not in ids
    # Ascending by occurred_at.
    dates = [item["occurred_at"] for item in body["items"]]
    assert dates == sorted(dates)


async def test_agenda_requires_auth(services: Services) -> None:
    app = create_app(services.config, services=services)
    with TestClient(app) as client:
        assert client.get("/agenda").status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_agenda.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `/agenda` returns 404 (route not defined).

- [ ] **Step 3: Add the `expand` param to `/timeline` and the new `/agenda` route**

In `src/saga/api/routes/timeline.py`:

(a) Add an `expand_recurrences` parameter to `_build_query` and pass it into the `EventQuery`. The function signature gains `expand_recurrences: bool = False`, and the returned `EventQuery(...)` gains `expand_recurrences=expand_recurrences`.

(b) Add an `expand` query param to `get_timeline` and forward it:

```python
    expand: Annotated[bool, Query(description="Expand recurring rules into occurrences.")] = False,
```
and in its `_build_query(...)` call add `expand_recurrences=expand,`.

(c) Add the agenda route (it needs `EventCategory` — already imported at module top — and `datetime`):

```python
@router.get("/agenda", response_model=TimelineResponse, summary="Upcoming events (recurrences expanded)")
async def get_agenda(
    services: ServicesDep,
    folder_id: Annotated[
        str | None, Query(description="Folder scope (subtree by default).")
    ] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=0)] = 0,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    cfg = services.config.timeline
    effective_limit = min(limit or cfg.default_page_size, cfg.max_page_size)
    query = EventQuery(
        categories=(EventCategory.CONTENT,),
        folder_id=folder_id,
        include_subtree=True,
        occurred_from=from_,
        occurred_to=to,
        order_by="occurred_at",
        descending=False,
        expand_recurrences=True,
        limit=effective_limit,
        offset=offset,
    )
    events = await services.timeline.query(query)
    return TimelineResponse(items=events, limit=query.limit, offset=query.offset)
```

NOTE: `EventCategory` is imported at the top of `timeline.py` (`from saga.core.models import EventCategory, EventType  # noqa: TC001`) and is available at runtime — use it directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_agenda.py -v -p no:cacheprovider --no-cov`
Expected: PASS (2 passed). Re-run `uv run pytest tests/test_api_timeline.py -p no:cacheprovider --no-cov` to confirm `/timeline` still works.

- [ ] **Step 5: Self-check + commit**

Run: `uv run ruff check src/saga/api/routes/timeline.py tests/test_api_agenda.py --fix && uv run mypy src/saga/api/routes/timeline.py`

```bash
git add src/saga/api/routes/timeline.py tests/test_api_agenda.py
git commit -m "feat: add GET /agenda and expand param on GET /timeline"
```

---

## Task 7: MCP `get_agenda` tool + prompt

**Files:**
- Modify: `src/saga/mcp/server.py`
- Create: `prompts/mcp/get_agenda.md`
- Test: `tests/test_mcp_get_agenda.py`

- [ ] **Step 1: Write the failing test**

Read `tests/test_mcp_get_timeline.py` first and **mirror its harness exactly** (its fixtures, the `_structured(...)` helper, and how it builds the MCP server with fake services). Create `tests/test_mcp_get_agenda.py` using that same setup, with these agenda-specific tests:

```python
async def test_get_agenda_tool_is_registered(config: AppConfig) -> None:
    mcp = <build server as in test_mcp_get_timeline.py>
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert "get_agenda" in names


async def test_get_agenda_tool_has_description(config: AppConfig) -> None:
    mcp = <build server as in test_mcp_get_timeline.py>
    tools = await mcp.list_tools()
    agenda = next(t for t in tools if t.name == "get_agenda")
    assert agenda.description  # loaded from prompts/mcp/get_agenda.md


async def test_get_agenda_returns_items(config: AppConfig) -> None:
    # Build the server with a fake/real TimelineService that returns one event,
    # exactly as test_mcp_get_timeline.py does for get_timeline.
    mcp = <build server with a timeline that yields one event>
    result = _structured(await mcp.call_tool("get_agenda", {}))
    assert "items" in result and "limit" in result and "offset" in result
```

Replace each `<...>` with the concrete setup copied from `tests/test_mcp_get_timeline.py` (same fixtures/imports). The point: registration, non-empty description, and a successful `call_tool("get_agenda", {})` returning the `{items, limit, offset}` shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_get_agenda.py -v -p no:cacheprovider --no-cov`
Expected: FAIL — `get_agenda` not registered / tool not found.

- [ ] **Step 3: Create the tool description**

Create `prompts/mcp/get_agenda.md`:

```markdown
Return upcoming events for the archive — a date-sorted agenda of what is coming up.

Combines future content events (appointments, deadlines, upcoming dated facts) with
**recurring obligations expanded into their concrete next occurrences** within a bounded
horizon. Recurring rules are expanded on the fly; you receive the individual occurrence
dates, not the abstract rule.

Filters:
- `folder_id` — restrict to a folder and its subtree.
- `limit` / `offset` — pagination.

Events are sorted ascending by their real-world date (`occurred_at`). Use this to answer
"what's coming up?", "what are my next deadlines?", or "what renews soon?".
```

- [ ] **Step 4: Add the `get_agenda` tool to the MCP server**

In `src/saga/mcp/server.py`, add this tool function next to `get_timeline` (it uses `EventQuery` and `EventCategory`, both already imported at the top of the file):

```python
    async def get_agenda(
        folder_id: Annotated[
            str | None, Field(description="Restrict to a folder (and its subtree).")
        ] = None,
        limit: Annotated[int, Field(description="Max events to return.")] = 50,
        offset: Annotated[int, Field(description="Pagination offset.")] = 0,
    ) -> dict[str, Any]:
        if services.timeline is None:
            return {"items": [], "limit": limit, "offset": offset}
        query = EventQuery(
            categories=(EventCategory.CONTENT,),
            folder_id=folder_id,
            include_subtree=True,
            order_by="occurred_at",
            descending=False,
            expand_recurrences=True,
            limit=min(limit, services.config.timeline.max_page_size),
            offset=offset,
        )
        events = await services.timeline.query(query)
        return {
            "items": [e.model_dump(mode="json") for e in events],
            "limit": query.limit,
            "offset": query.offset,
        }
```

Then register it in the `tools` list (the `list[Callable[..., Any]]` that includes `get_timeline`) by adding `get_agenda,` right after `get_timeline,`.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_get_agenda.py -v -p no:cacheprovider --no-cov`
Expected: PASS.

- [ ] **Step 6: Self-check + commit**

Run: `uv run ruff check src/saga/mcp/server.py tests/test_mcp_get_agenda.py --fix && uv run mypy src/saga/mcp/server.py`

```bash
git add src/saga/mcp/server.py prompts/mcp/get_agenda.md tests/test_mcp_get_agenda.py
git commit -m "feat: add get_agenda MCP tool"
```

---

## Task 8: Final gates

**Files:** none (verification only).

- [ ] **Step 1: Lint, type-check, full suite**

Run, in order:
```
uv run ruff check . --fix
uv run ruff format .
uv run mypy
uv run pytest
```
Expected: ruff clean; if `ruff format` reformats files, include them in a follow-up commit; mypy reports no errors (incl. tests — run the FULL `uv run mypy`, since per-file runs miss test-file type errors); all tests pass with coverage ≥ 80% on core packages.

- [ ] **Step 2: Docker build gate**

From the workspace root `d:\Projekte\Archiv` (Bash): `cd /d/Projekte/Archiv && docker compose build api worker`
Expected: exit code 0.

- [ ] **Step 3: Commit any formatting fixups**

If Step 1 changed tracked files:
```bash
git add -A
git commit -m "style: ruff format Phase 3 files"
```

---

## Self-Review

**1. Spec coverage (against `2026-06-17-timeline-phase3-recurrence-design.md`):**
- §3 architecture — pure `expand_recurrences` (Task 3) + `TimelineService` expansion path with separate rule fetch / merge / sort (Task 5) + agenda surface (Tasks 6, 7). ✓
- §4 expansion semantics — `rrulestr(dtstart=anchor).between(...)`, `end_date` bound, malformed skip + log, safety cap, synthetic `<id>@<date>` + `occurrence_of`, pass-through (Task 3 + its tests). ✓
- §5 extraction validation — `validate_rrule` (Task 1) + `field_validator` on `TimelineEventOut` (Task 2); `python-dateutil` direct dep (Task 1). ✓
- §6 agenda surface — `EventQuery.expand_recurrences` (Task 5), `GET /agenda` + `/timeline?expand` (Task 6), MCP `get_agenda` + prompt (Task 7). ✓
- §7 config + errors — `TimelineConfig` fields + `config.yaml` (Task 4); read-time skip logs via `saga.timeline`, extraction raises `ValueError` (Tasks 2, 3). ✓
- §8 testing — validate_rrule, schema validator, expand_recurrences (in/out window, end_date, malformed, cap), service integration (past anchor), `/agenda` + `/timeline?expand`, MCP tool; gates in Task 8. ✓

**2. Placeholder scan:** No TBD/"handle edge cases". The only `<...>` placeholders are in Task 7's test, with an explicit instruction to copy the concrete harness from `tests/test_mcp_get_timeline.py` (a real, named file) — not an invented stub.

**3. Type consistency:** `expand_recurrences(rules, *, window_start, window_end, max_occurrences) -> list[Event]` is identical in Task 3 (definition) and Task 5 (call). `TimelineService(store, *, recurrence_horizon_days=, max_occurrences_per_rule=)` matches across Task 5 (definition), Task 5 Step 6 (app.py), Task 6 (test fixture). `EventQuery.expand_recurrences: bool` used consistently. `TimelineConfig.recurrence_horizon_days` / `max_occurrences_per_rule` names match across Tasks 4, 5, 6. The synthetic id format `<rule_id>@<YYYY-MM-DD>` matches in Task 3 impl + Task 3/5/6 assertions.
