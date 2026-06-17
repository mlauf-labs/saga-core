# Timeline & Event Log — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tagged, append-only event store to `saga-core` and emit a deterministic **audit stream** (placement / reclassification / move / folder-created / doc-ingested, with placement rationale), exposed through one read path over REST and MCP.

**Architecture:** A new `events` table (Postgres, system of record) mirrored as a Pydantic `Event`. A thin `EventRecorder` (best-effort, never fails ingestion) is called from pipeline stages and the shared service layer. A `TimelineService` over `PostgresStore.query_events` is the single read path used by a new `GET /timeline` route, `GET /documents/{id}/timeline`, and an MCP `get_timeline` tool. The content/LLM stream and recurrence are **Phase 2/3**, out of scope here; the schema already accommodates them.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async ORM (asyncpg; aiosqlite in tests), Alembic, FastAPI, ARQ, FastMCP, Pydantic v2, `uv`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-16-timeline-event-log-design.md` (§3 model, §4 audit stream, §6 read layer, §7 phasing — Phase 1).

**Conventions (from `CLAUDE.md`):** fully typed, `uv run mypy` strict; `uv run ruff check . --fix` + `uv run ruff format .`; tests ≥80% on core packages with external services mocked; config over constants; prompts in `prompts/*.md`; errors from `saga.core.errors`; structured logging via `saga.core.logging.get_logger`; English only. All paths below are relative to the `saga-core/` repo root.

---

## Setup (do once, before Task 1)

- [ ] **Create a feature branch** (saga-core protects `main`/`develop`; never push to them directly).

```bash
git switch develop && git pull
git switch -c feature/timeline-event-log
```

- [ ] **Confirm the current Alembic head is `0003_merge_branches`** (the new migration depends on it).

Run: `uv run alembic heads`
Expected: a single head `0003_merge_branches`. If there are multiple heads, stop and resolve before continuing.

---

## File Structure

**New files**
- `src/saga/events/__init__.py` — package exports (`EventRecorder`, `TimelineService`, `EventQuery`, `EventSink`, `TimelineStore`)
- `src/saga/events/recorder.py` — `EventRecorder` + `EventSink` protocol (write path)
- `src/saga/events/service.py` — `TimelineService` + `EventQuery` + `TimelineStore` protocol (read path)
- `src/saga/api/routes/timeline.py` — `GET /timeline`, `GET /documents/{id}/timeline`
- `migrations/versions/0004_add_events.py` — `events` table migration
- `prompts/mcp/get_timeline.md` — MCP tool description
- `tests/events/test_recorder.py`, `tests/events/test_service.py`
- `tests/storage/test_events_store.py`
- `tests/api/routes/test_timeline.py`
- `tests/pipeline/test_event_emission.py`

**Modified files**
- `src/saga/core/models.py` — `EventCategory`, `EventType`, `Event`
- `src/saga/storage/postgres.py` — `EventRow`, `_to_event`, `append_event`, `query_events`
- `src/saga/core/config.py` — `TimelineConfig` on `AppConfig`
- `config/config.yaml` — `timeline:` section
- `src/saga/api/schemas.py` — `TimelineResponse`
- `src/saga/api/dependencies.py` — add `events` + `timeline` fields to `Services`
- `src/saga/api/app.py` — construct `EventRecorder` + `TimelineService`, include the timeline router
- `src/saga/api/service.py` — emit `move` / `folder_created` events
- `src/saga/pipeline/worker.py` — build `EventRecorder` into the worker ctx
- `src/saga/pipeline/tasks.py` — pass `events` + `similar` to stages, emit `doc_ingested`
- `src/saga/pipeline/stages.py` — emit `placement` and `reclassification`
- `src/saga/mcp/server.py` — `get_timeline` tool

---

## Task 1: Event domain model

**Files:**
- Modify: `src/saga/core/models.py` (add enums + model near the top, after the imports / `DocumentStatus`)
- Test: `tests/core/test_event_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_event_model.py
from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType


def test_event_defaults_and_roundtrip():
    now = datetime(2026, 5, 1, tzinfo=UTC)
    event = Event(
        event_id="e1",
        category=EventCategory.AUDIT,
        event_type=EventType.PLACEMENT,
        document_id="d1",
        folder_id="f1",
        occurred_at=now,
        recorded_at=now,
        actor="pipeline",
        summary="Placed in Finance",
        dedupe_key="placement:d1:f1",
        details={"votes": [{"folder_id": "f1", "score": 0.7}]},
    )
    assert event.confidence is None
    dumped = event.model_dump(mode="json")
    assert dumped["category"] == "audit"
    assert dumped["event_type"] == "placement"
    assert Event.model_validate(dumped).details["votes"][0]["score"] == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_event_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'Event'`.

- [ ] **Step 3: Add the model to `src/saga/core/models.py`**

Add these to the existing imports (top of file already imports `datetime`, `StrEnum`, `BaseModel`, `Field`):

```python
from typing import Any  # add to the existing typing import block if not present
```

Add after the `DocumentStatus` enum:

```python
class EventCategory(StrEnum):
    """Which view an event belongs to (see timeline design §3)."""

    AUDIT = "audit"
    CONTENT = "content"


class EventType(StrEnum):
    """The kind of event. Audit types are emitted by code; content types by the LLM."""

    # audit (Phase 1)
    DOC_INGESTED = "doc_ingested"
    PLACEMENT = "placement"
    MOVE = "move"
    RECLASSIFICATION = "reclassification"
    FOLDER_CREATED = "folder_created"
    FOLDER_RENAMED = "folder_renamed"
    # content (Phase 2/3)
    DATED_FACT = "dated_fact"
    APPOINTMENT = "appointment"
    RECURRING = "recurring"


class Event(BaseModel):
    """A single timeline event (system of record: Postgres ``events`` table).

    ``occurred_at`` is the event time (content: real-world date; audit: equals
    ``recorded_at``). ``recorded_at`` is the archive time the row was written.
    ``dedupe_key`` makes re-emitted identical audit events a no-op.
    """

    event_id: str
    category: EventCategory
    event_type: EventType
    document_id: str | None = None
    folder_id: str | None = None
    occurred_at: datetime | None = None
    recorded_at: datetime
    actor: str
    summary: str
    confidence: float | None = None
    dedupe_key: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_event_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saga/core/models.py tests/core/test_event_model.py
git commit -m "feat: add Event domain model for timeline/audit log"
```

---

## Task 2: `events` table — ORM row + Alembic migration

**Files:**
- Modify: `src/saga/storage/postgres.py` (add `EventRow` after `DocumentFolderRow`, and a `_to_event` helper near the other `_to_*` helpers)
- Create: `migrations/versions/0004_add_events.py`
- Test: `tests/storage/test_events_store.py` (table-creation smoke test; extended in Tasks 3–4)

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_events_store.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from saga.storage.postgres import PostgresStore


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://")
    s = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await s.bootstrap()
    yield s
    await s.close()


async def test_events_table_is_created(store):
    # query_events on an empty store returns no rows (table exists).
    assert await store.query_events() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_events_store.py -v`
Expected: FAIL — `AttributeError: 'PostgresStore' object has no attribute 'query_events'` (added in Task 4) or a missing-table error. Either failure is acceptable here.

- [ ] **Step 3: Add the ORM row + index to `src/saga/storage/postgres.py`**

Add `Index` to the existing `from sqlalchemy import (...)` block. Then add after `DocumentFolderRow`:

```python
class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_document_id", "document_id"),
        Index("ix_events_folder_id", "folder_id"),
        Index("ix_events_category", "category"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_recorded_at", "recorded_at"),
        # NULLs are distinct in both Postgres and SQLite, so content events
        # (dedupe_key NULL, Phase 2) never collide; audit events de-duplicate.
        Index("uq_events_dedupe_key", "dedupe_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    folder_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    actor: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(_JSON, default=dict, nullable=False)
```

> Note: `document_id` / `folder_id` are intentionally **plain columns without foreign keys** — the audit log is an append-only history that may outlive the entities it references. Cleanup on document delete is out of scope for Phase 1.

Add the converter near the other `_to_*` helpers (e.g. after `_to_folder`):

```python
def _to_event(row: EventRow) -> Event:
    return Event(
        event_id=row.id,
        category=EventCategory(row.category),
        event_type=EventType(row.event_type),
        document_id=row.document_id,
        folder_id=row.folder_id,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        actor=row.actor,
        summary=row.summary,
        confidence=row.confidence,
        dedupe_key=row.dedupe_key,
        details=dict(row.details or {}),
    )
```

Add the new model imports to the existing `from saga.core.models import (...)` block:

```python
    Event,
    EventCategory,
    EventType,
```

- [ ] **Step 4: Create the Alembic migration `migrations/versions/0004_add_events.py`**

```python
"""Add the events table (timeline/audit log).

Revision ID: 0004_add_events
Revises: 0003_merge_branches
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004_add_events"
down_revision: str | None = "0003_merge_branches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("folder_id", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
    )
    op.create_index("ix_events_document_id", "events", ["document_id"])
    op.create_index("ix_events_folder_id", "events", ["folder_id"])
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_recorded_at", "events", ["recorded_at"])
    op.create_index("uq_events_dedupe_key", "events", ["dedupe_key"], unique=True)


def downgrade() -> None:
    op.drop_table("events")
```

- [ ] **Step 5: Run the smoke test (table now created via `Base.metadata.create_all`)**

Run: `uv run pytest tests/storage/test_events_store.py -v`
Expected: still FAIL on `query_events` not existing — that's Task 4. Confirm the failure is specifically `AttributeError: ... 'query_events'`, not a SQL/table error.

- [ ] **Step 6: Commit**

```bash
git add src/saga/storage/postgres.py migrations/versions/0004_add_events.py tests/storage/test_events_store.py
git commit -m "feat: add events table ORM row and migration"
```

---

## Task 3: `PostgresStore.append_event` (with portable de-duplication)

**Files:**
- Modify: `src/saga/storage/postgres.py` (add method to `PostgresStore`, e.g. after `set_primary_folder`)
- Test: `tests/storage/test_events_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_events_store.py  (append)
from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType


def _audit(dedupe_key: str | None) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    return Event(
        event_id="",  # store assigns one
        category=EventCategory.AUDIT,
        event_type=EventType.PLACEMENT,
        document_id="d1",
        folder_id="f1",
        occurred_at=now,
        recorded_at=now,
        actor="pipeline",
        summary="Placed in Finance",
        dedupe_key=dedupe_key,
    )


async def test_append_event_dedupes_on_key(store):
    assert await store.append_event(_audit("placement:d1:f1")) is True
    assert await store.append_event(_audit("placement:d1:f1")) is False  # duplicate
    rows = await store.query_events()
    assert len(rows) == 1


async def test_append_event_without_key_always_inserts(store):
    assert await store.append_event(_audit(None)) is True
    assert await store.append_event(_audit(None)) is True
    assert len(await store.query_events()) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_events_store.py -k append_event -v`
Expected: FAIL — `AttributeError: ... 'append_event'`.

- [ ] **Step 3: Implement `append_event`**

```python
    async def append_event(self, event: Event) -> bool:
        """Persist *event*; return ``False`` (no-op) if its ``dedupe_key`` already exists.

        Uses a portable check-then-insert (works on Postgres and the sqlite test
        engine). Placement runs under a global Redis lock and re-ingest is sequential
        per document, so the check-then-insert race window is not a concern; the unique
        index on ``dedupe_key`` is the backstop.
        """
        async with self._sessions()() as session, session.begin():
            if event.dedupe_key is not None:
                existing = (
                    await session.execute(
                        select(EventRow.id).where(EventRow.dedupe_key == event.dedupe_key)
                    )
                ).first()
                if existing is not None:
                    return False
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
            return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/storage/test_events_store.py -k append_event -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/saga/storage/postgres.py tests/storage/test_events_store.py
git commit -m "feat: add PostgresStore.append_event with de-duplication"
```

---

## Task 4: `PostgresStore.query_events`

**Files:**
- Modify: `src/saga/storage/postgres.py` (add method after `append_event`)
- Test: `tests/storage/test_events_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_events_store.py  (append)
from saga.core.models import Event, EventCategory, EventType


def _event(**kw) -> Event:
    now = datetime(2026, 5, 1, tzinfo=UTC)
    base = dict(
        event_id="",
        category=EventCategory.AUDIT,
        event_type=EventType.PLACEMENT,
        document_id="d1",
        folder_id="f1",
        occurred_at=now,
        recorded_at=now,
        actor="pipeline",
        summary="s",
        dedupe_key=None,
    )
    base.update(kw)
    return Event(**base)  # type: ignore[arg-type]


async def test_query_events_filters_by_category_and_document(store):
    await store.append_event(_event(event_type=EventType.PLACEMENT, document_id="d1"))
    await store.append_event(_event(event_type=EventType.MOVE, document_id="d2", folder_id="f2"))

    only_d1 = await store.query_events(document_id="d1")
    assert [e.document_id for e in only_d1] == ["d1"]

    audits = await store.query_events(categories=[EventCategory.AUDIT])
    assert len(audits) == 2

    moves = await store.query_events(event_types=[EventType.MOVE])
    assert [e.event_type for e in moves] == [EventType.MOVE]


async def test_query_events_filters_by_folder_ids_and_limits(store):
    for fid in ("f1", "f2", "f3"):
        await store.append_event(_event(folder_id=fid))
    hits = await store.query_events(folder_ids=["f1", "f3"])
    assert sorted(e.folder_id for e in hits) == ["f1", "f3"]
    assert len(await store.query_events(limit=1)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/storage/test_events_store.py -k query_events -v`
Expected: FAIL — `AttributeError: ... 'query_events'`.

- [ ] **Step 3: Implement `query_events`**

Add `Literal` to the typing import at the top of the file (`from typing import TYPE_CHECKING, Any, Literal`):

```python
    async def query_events(
        self,
        *,
        categories: Sequence[EventCategory] | None = None,
        event_types: Sequence[EventType] | None = None,
        document_id: str | None = None,
        folder_ids: Sequence[str] | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        order_by: Literal["recorded_at", "occurred_at"] = "recorded_at",
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Event]:
        """Read events with optional filters. Folder subtree expansion is the caller's job."""
        stmt = select(EventRow)
        if categories:
            stmt = stmt.where(EventRow.category.in_([str(c) for c in categories]))
        if event_types:
            stmt = stmt.where(EventRow.event_type.in_([str(t) for t in event_types]))
        if document_id is not None:
            stmt = stmt.where(EventRow.document_id == document_id)
        if folder_ids is not None:
            stmt = stmt.where(EventRow.folder_id.in_(list(folder_ids)))
        if occurred_from is not None:
            stmt = stmt.where(EventRow.occurred_at >= occurred_from)
        if occurred_to is not None:
            stmt = stmt.where(EventRow.occurred_at <= occurred_to)
        sort_col = EventRow.occurred_at if order_by == "occurred_at" else EventRow.recorded_at
        stmt = stmt.order_by(sort_col.desc() if descending else sort_col.asc())
        stmt = stmt.limit(limit).offset(offset)
        async with self._sessions()() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_to_event(row) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/storage/test_events_store.py -v`
Expected: PASS (all event-store tests, including Task 2's smoke test).

- [ ] **Step 5: Commit**

```bash
git add src/saga/storage/postgres.py tests/storage/test_events_store.py
git commit -m "feat: add PostgresStore.query_events with filters"
```

---

## Task 5: `EventRecorder` (best-effort write path)

**Files:**
- Create: `src/saga/events/__init__.py`, `src/saga/events/recorder.py`
- Test: `tests/events/test_recorder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/events/test_recorder.py
from saga.core.models import Event, EventCategory, EventType, FolderVote, SimilarDocument
from saga.events import EventRecorder


class _Sink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[Event] = []
        self._fail = fail

    async def append_event(self, event: Event) -> bool:
        if self._fail:
            raise RuntimeError("db down")
        self.events.append(event)
        return True


async def test_record_placement_builds_audit_event_with_rationale():
    sink = _Sink()
    recorder = EventRecorder(sink, rationale_top_n=2)
    similar = [
        SimilarDocument(document_id="a", title="KFZ-Police 2025", score=0.9),
        SimilarDocument(document_id="b", title="Hausrat 2024", score=0.8),
        SimilarDocument(document_id="c", title="Ignored", score=0.1),
    ]
    votes = [FolderVote(folder_id="f1", score=0.7)]
    await recorder.record_placement(
        document_id="d1", folders=["f1"], primary="f1", similar=similar, votes=votes
    )
    [event] = sink.events
    assert event.category == EventCategory.AUDIT
    assert event.event_type == EventType.PLACEMENT
    assert event.actor == "pipeline"
    assert event.dedupe_key == "placement:d1:f1"
    assert [s["title"] for s in event.details["similar"]] == ["KFZ-Police 2025", "Hausrat 2024"]
    assert "KFZ-Police 2025" in event.summary


async def test_recorder_never_raises_when_sink_fails():
    recorder = EventRecorder(_Sink(fail=True))
    # Must not raise — ingestion must never fail because of event recording.
    await recorder.record_doc_ingested(document_id="d1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_recorder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'saga.events'`.

- [ ] **Step 3: Create `src/saga/events/recorder.py`**

```python
"""Best-effort emission of audit events (timeline design §4).

The recorder builds typed :class:`Event` objects and writes them through an
:class:`EventSink`. Writing an event must NEVER fail the ingestion pipeline or an
API mutation, so every write is wrapped and failures are logged, not raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saga.core.logging import get_logger
from saga.core.models import Event, EventCategory, EventType
from saga.storage.postgres import _new_id, _now

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.models import FolderVote, SimilarDocument

_log = get_logger("saga.events")


class EventSink(Protocol):
    """The single write operation the recorder depends on (satisfied by PostgresStore)."""

    async def append_event(self, event: Event) -> bool: ...


class EventRecorder:
    """Builds and persists audit events. All methods are best-effort (never raise)."""

    def __init__(self, sink: EventSink, *, rationale_top_n: int = 5) -> None:
        self._sink = sink
        self._top_n = rationale_top_n

    async def _safe_append(self, event: Event) -> None:
        try:
            await self._sink.append_event(event)
        except Exception as exc:  # noqa: BLE001 - emission must never break the caller
            _log.warning("event_append_failed", event_type=str(event.event_type), error=str(exc))

    def _new(
        self,
        *,
        event_type: EventType,
        actor: str,
        summary: str,
        category: EventCategory = EventCategory.AUDIT,
        document_id: str | None = None,
        folder_id: str | None = None,
        dedupe_key: str | None = None,
        details: dict[str, object] | None = None,
    ) -> Event:
        now = _now()
        return Event(
            event_id=_new_id(),
            category=category,
            event_type=event_type,
            document_id=document_id,
            folder_id=folder_id,
            occurred_at=now,  # audit: occurred == recorded
            recorded_at=now,
            actor=actor,
            summary=summary,
            dedupe_key=dedupe_key,
            details=dict(details or {}),
        )

    async def record_doc_ingested(self, *, document_id: str, actor: str = "pipeline") -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.DOC_INGESTED,
                actor=actor,
                summary="Document ingested.",
                document_id=document_id,
                dedupe_key=f"doc_ingested:{document_id}",
            )
        )

    async def record_placement(
        self,
        *,
        document_id: str,
        folders: Sequence[str],
        primary: str | None,
        similar: Sequence[SimilarDocument],
        votes: Sequence[FolderVote],
        reason: str | None = None,
        actor: str = "pipeline",
    ) -> None:
        if not folders:
            return
        top = list(similar)[: self._top_n]
        similar_titles = [s.title for s in top]
        because = f" — similar to {', '.join(repr(t) for t in similar_titles)}" if top else ""
        details: dict[str, object] = {
            "similar": [
                {"document_id": s.document_id, "title": s.title, "score": s.score} for s in top
            ],
            "votes": [{"folder_id": v.folder_id, "score": v.score} for v in votes],
            "assignments": list(folders),
            "primary": primary,
        }
        if reason:
            details["reason"] = reason
        await self._safe_append(
            self._new(
                event_type=EventType.PLACEMENT,
                actor=actor,
                summary=f"Placed in {len(folders)} folder(s){because}.",
                document_id=document_id,
                folder_id=primary,
                dedupe_key=f"placement:{document_id}:{','.join(sorted(folders))}",
                details=details,
            )
        )

    async def record_reclassification(
        self,
        *,
        document_id: str,
        from_doc_type: str | None,
        to_doc_type: str,
        actor: str = "pipeline",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.RECLASSIFICATION,
                actor=actor,
                summary=f"Classified as '{to_doc_type}'"
                + (f" (was '{from_doc_type}')." if from_doc_type else "."),
                document_id=document_id,
                dedupe_key=f"reclassification:{document_id}:{to_doc_type}",
                details={"from_doc_type": from_doc_type, "to_doc_type": to_doc_type},
            )
        )

    async def record_move(
        self,
        *,
        document_id: str,
        from_folders: Sequence[str],
        to_folders: Sequence[str],
        primary: str | None,
        actor: str = "user",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.MOVE,
                actor=actor,
                summary=f"Moved to {len(to_folders)} folder(s).",
                document_id=document_id,
                folder_id=primary,
                dedupe_key=None,  # explicit moves are always recorded
                details={
                    "from_folders": list(from_folders),
                    "to_folders": list(to_folders),
                    "primary": primary,
                },
            )
        )

    async def record_folder_created(
        self,
        *,
        folder_id: str,
        name: str,
        parent_id: str | None,
        actor: str = "user",
    ) -> None:
        await self._safe_append(
            self._new(
                event_type=EventType.FOLDER_CREATED,
                actor=actor,
                summary=f"Folder '{name}' created.",
                folder_id=folder_id,
                dedupe_key=f"folder_created:{folder_id}",
                details={"name": name, "parent_id": parent_id},
            )
        )
```

- [ ] **Step 4: Create `src/saga/events/__init__.py`** (also re-exports Task 6's symbols; create the file now with the recorder exports, extend in Task 6)

```python
"""Timeline & audit event subsystem (write + read paths)."""

from __future__ import annotations

from saga.events.recorder import EventRecorder, EventSink

__all__ = ["EventRecorder", "EventSink"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/events/test_recorder.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/saga/events/__init__.py src/saga/events/recorder.py tests/events/test_recorder.py
git commit -m "feat: add EventRecorder for best-effort audit emission"
```

---

## Task 6: `TimelineService` (read path with folder-subtree expansion)

**Files:**
- Create: `src/saga/events/service.py`
- Modify: `src/saga/events/__init__.py` (export `TimelineService`, `EventQuery`, `TimelineStore`)
- Test: `tests/events/test_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/events/test_service.py
from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType
from saga.events import EventQuery, TimelineService


class _Store:
    """Fake TimelineStore: records the folder_ids passed and returns canned events."""

    def __init__(self, parents: dict[str, str | None]) -> None:
        self._parents = parents
        self.seen_folder_ids: list[str] | None = None

    async def parents_map(self) -> dict[str, str | None]:
        return self._parents

    async def query_events(self, **kwargs) -> list[Event]:
        self.seen_folder_ids = (
            list(kwargs["folder_ids"]) if kwargs.get("folder_ids") is not None else None
        )
        now = datetime(2026, 5, 1, tzinfo=UTC)
        return [
            Event(
                event_id="e1",
                category=EventCategory.AUDIT,
                event_type=EventType.PLACEMENT,
                recorded_at=now,
                actor="pipeline",
                summary="s",
            )
        ]


async def test_query_expands_folder_subtree():
    # f_child's parent is f_root; querying f_root with subtree must include both.
    store = _Store({"f_root": None, "f_child": "f_root"})
    service = TimelineService(store)
    await service.query(EventQuery(folder_id="f_root", include_subtree=True))
    assert sorted(store.seen_folder_ids) == ["f_child", "f_root"]


async def test_query_without_subtree_uses_single_folder():
    store = _Store({"f_root": None, "f_child": "f_root"})
    service = TimelineService(store)
    await service.query(EventQuery(folder_id="f_root", include_subtree=False))
    assert store.seen_folder_ids == ["f_root"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/events/test_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'EventQuery'`.

- [ ] **Step 3: Create `src/saga/events/service.py`**

```python
"""Read path for the timeline/audit log (timeline design §6).

A single :class:`TimelineService` used by the REST routes, the MCP tool, and (later)
the OKF exporter. Folder filters are expanded to include the subtree here, so the
store stays a simple ``folder_id IN (...)`` query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol

from saga.storage.postgres import descendant_ids

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saga.core.models import Event, EventCategory, EventType


class TimelineStore(Protocol):
    """The read operations the service depends on (satisfied by PostgresStore)."""

    async def parents_map(self) -> dict[str, str | None]: ...

    async def query_events(
        self,
        *,
        categories: Sequence[EventCategory] | None = ...,
        event_types: Sequence[EventType] | None = ...,
        document_id: str | None = ...,
        folder_ids: Sequence[str] | None = ...,
        occurred_from: datetime | None = ...,
        occurred_to: datetime | None = ...,
        order_by: Literal["recorded_at", "occurred_at"] = ...,
        descending: bool = ...,
        limit: int = ...,
        offset: int = ...,
    ) -> list[Event]: ...


@dataclass(frozen=True)
class EventQuery:
    """Filters for a timeline query (the 'which view' switch is ``categories``)."""

    categories: tuple[EventCategory, ...] | None = None
    event_types: tuple[EventType, ...] | None = None
    document_id: str | None = None
    folder_id: str | None = None
    include_subtree: bool = True
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    order_by: Literal["recorded_at", "occurred_at"] = "recorded_at"
    descending: bool = True
    limit: int = 100
    offset: int = 0


class TimelineService:
    """Resolves folder subtrees and delegates to the store's ``query_events``."""

    def __init__(self, store: TimelineStore) -> None:
        self._store = store

    async def query(self, q: EventQuery) -> list[Event]:
        folder_ids: list[str] | None = None
        if q.folder_id is not None:
            if q.include_subtree:
                parents = await self._store.parents_map()
                folder_ids = descendant_ids(q.folder_id, parents)
            else:
                folder_ids = [q.folder_id]
        return await self._store.query_events(
            categories=q.categories,
            event_types=q.event_types,
            document_id=q.document_id,
            folder_ids=folder_ids,
            occurred_from=q.occurred_from,
            occurred_to=q.occurred_to,
            order_by=q.order_by,
            descending=q.descending,
            limit=q.limit,
            offset=q.offset,
        )
```

- [ ] **Step 4: Update `src/saga/events/__init__.py`**

```python
"""Timeline & audit event subsystem (write + read paths)."""

from __future__ import annotations

from saga.events.recorder import EventRecorder, EventSink
from saga.events.service import EventQuery, TimelineService, TimelineStore

__all__ = ["EventQuery", "EventRecorder", "EventSink", "TimelineService", "TimelineStore"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/events/test_service.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/saga/events/service.py src/saga/events/__init__.py tests/events/test_service.py
git commit -m "feat: add TimelineService read path with subtree expansion"
```

---

## Task 7: `TimelineConfig`

**Files:**
- Modify: `src/saga/core/config.py` (add a `TimelineConfig` Pydantic model and a `timeline` field on `AppConfig`, following the existing nested-config pattern, e.g. `SimilarityConfig`)
- Modify: `config/config.yaml` (add a `timeline:` section)
- Test: `tests/core/test_config_timeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_config_timeline.py
from saga.core.config import load_config


def test_timeline_config_defaults_present():
    cfg = load_config()
    assert cfg.timeline.rationale_top_n >= 1
    assert cfg.timeline.default_page_size >= 1
    assert cfg.timeline.max_page_size >= cfg.timeline.default_page_size
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_config_timeline.py -v`
Expected: FAIL — `AttributeError: 'AppConfig' object has no attribute 'timeline'`.

- [ ] **Step 3: Add `TimelineConfig` in `src/saga/core/config.py`**

Find the existing nested config models (e.g. `class SimilarityConfig(BaseModel): ...`) and add alongside them:

```python
class TimelineConfig(BaseModel):
    """Read/emit tuning for the timeline/audit log."""

    rationale_top_n: int = 5
    default_page_size: int = 50
    max_page_size: int = 500
```

Add the field to `AppConfig` (mirror how `similarity: SimilarityConfig = ...` is declared):

```python
    timeline: TimelineConfig = TimelineConfig()
```

- [ ] **Step 4: Add to `config/config.yaml`** (top-level key, matching the YAML style of the existing sections)

```yaml
timeline:
  rationale_top_n: 5
  default_page_size: 50
  max_page_size: 500
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/core/test_config_timeline.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/saga/core/config.py config/config.yaml tests/core/test_config_timeline.py
git commit -m "feat: add TimelineConfig (rationale fan-out, pagination)"
```

---

## Task 8: `TimelineResponse` schema

**Files:**
- Modify: `src/saga/api/schemas.py` (add near the other list/page response models)
- Test: covered by Task 9's route test.

- [ ] **Step 1: Add the schema**

```python
# src/saga/api/schemas.py  (add)
from saga.core.models import Event  # add to the existing models import if not present


class TimelineResponse(BaseModel):
    """A page of timeline events (audit and/or content)."""

    items: list[Event]
    limit: int
    offset: int
```

- [ ] **Step 2: Type-check**

Run: `uv run mypy src/saga/api/schemas.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/saga/api/schemas.py
git commit -m "feat: add TimelineResponse schema"
```

---

## Task 9: Timeline REST routes

**Files:**
- Create: `src/saga/api/routes/timeline.py`
- Test: `tests/api/routes/test_timeline.py`

> The route reads `services.timeline` (wired in Task 10). The test builds a real `TimelineService` over a sqlite-backed `PostgresStore` and injects a `Services` instance — matching the existing injected-services test pattern in `create_app(..., services=...)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/routes/test_timeline.py
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from saga.api.app import create_app
from saga.api.dependencies import Services
from saga.core.config import load_config
from saga.core.models import Event, EventCategory, EventType
from saga.events import EventRecorder, TimelineService
from saga.storage.postgres import PostgresStore


@pytest.fixture
async def client():
    cfg = load_config()
    cfg.security.tokens = []  # disable auth for the test
    engine = create_async_engine("sqlite+aiosqlite://")
    db = PostgresStore(config=None, engine=engine)  # type: ignore[arg-type]
    await db.bootstrap()
    now = datetime(2026, 5, 1, tzinfo=UTC)
    await db.append_event(
        Event(
            event_id="",
            category=EventCategory.AUDIT,
            event_type=EventType.PLACEMENT,
            document_id="d1",
            folder_id="f1",
            occurred_at=now,
            recorded_at=now,
            actor="pipeline",
            summary="Placed in Finance",
        )
    )
    services = Services(
        config=cfg,
        db=db,
        opensearch=None,  # type: ignore[arg-type]
        minio=None,  # type: ignore[arg-type]
        queue=None,  # type: ignore[arg-type]
        search=None,  # type: ignore[arg-type]
        events=EventRecorder(db),
        timeline=TimelineService(db),
    )
    app = create_app(config=cfg, services=services)
    with TestClient(app) as c:
        yield c


def test_get_timeline_returns_events(client):
    resp = client.get("/timeline", params={"document_id": "d1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["event_type"] == "placement"
    assert body["items"][0]["summary"] == "Placed in Finance"


def test_get_document_timeline(client):
    resp = client.get("/documents/d1/timeline")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/routes/test_timeline.py -v`
Expected: FAIL — 404 (route not registered) and/or `TypeError` on the new `Services(events=..., timeline=...)` keywords (added in Task 10).

- [ ] **Step 3: Create `src/saga/api/routes/timeline.py`**

```python
"""Timeline endpoints: one read path over the tagged event store (design §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from saga.api.dependencies import AuthDep, ServicesDep
from saga.api.schemas import TimelineResponse
from saga.core.errors import SagaError
from saga.core.models import EventCategory, EventType
from saga.events import EventQuery

router = APIRouter(tags=["timeline"], dependencies=[AuthDep])


def _build_query(
    services: ServicesDep,
    *,
    document_id: str | None,
    folder_id: str | None,
    include_subtree: bool,
    categories: list[EventCategory] | None,
    event_types: list[EventType] | None,
    occurred_from: datetime | None,
    occurred_to: datetime | None,
    order_by: str,
    limit: int,
    offset: int,
) -> EventQuery:
    cfg = services.config.timeline
    effective_limit = min(limit or cfg.default_page_size, cfg.max_page_size)
    return EventQuery(
        categories=tuple(categories) if categories else None,
        event_types=tuple(event_types) if event_types else None,
        document_id=document_id,
        folder_id=folder_id,
        include_subtree=include_subtree,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        order_by="occurred_at" if order_by == "occurred_at" else "recorded_at",
        limit=effective_limit,
        offset=offset,
    )


@router.get("/timeline", response_model=TimelineResponse, summary="Query the timeline/audit log")
async def get_timeline(
    services: ServicesDep,
    document_id: Annotated[str | None, Query()] = None,
    folder_id: Annotated[str | None, Query(description="Folder scope (subtree by default).")] = None,
    include_subtree: Annotated[bool, Query()] = True,
    category: Annotated[list[EventCategory] | None, Query()] = None,
    event_type: Annotated[list[EventType] | None, Query()] = None,
    occurred_from: Annotated[datetime | None, Query()] = None,
    occurred_to: Annotated[datetime | None, Query()] = None,
    order_by: Annotated[str, Query(pattern="^(recorded_at|occurred_at)$")] = "recorded_at",
    limit: Annotated[int, Query(ge=0)] = 0,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    query = _build_query(
        services,
        document_id=document_id,
        folder_id=folder_id,
        include_subtree=include_subtree,
        categories=category,
        event_types=event_type,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    events = await services.timeline.query(query)
    return TimelineResponse(items=events, limit=query.limit, offset=query.offset)


@router.get(
    "/documents/{document_id}/timeline",
    response_model=TimelineResponse,
    summary="Timeline of a single document",
)
async def get_document_timeline(
    services: ServicesDep,
    document_id: str,
    category: Annotated[list[EventCategory] | None, Query()] = None,
    order_by: Annotated[str, Query(pattern="^(recorded_at|occurred_at)$")] = "recorded_at",
    limit: Annotated[int, Query(ge=0)] = 0,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    if services.timeline is None:  # pragma: no cover - defensive
        raise SagaError("Timeline service is not initialised.")
    query = _build_query(
        services,
        document_id=document_id,
        folder_id=None,
        include_subtree=False,
        categories=category,
        event_types=None,
        occurred_from=None,
        occurred_to=None,
        order_by=order_by,
        limit=limit,
        offset=offset,
    )
    events = await services.timeline.query(query)
    return TimelineResponse(items=events, limit=query.limit, offset=query.offset)
```

- [ ] **Step 4: Run test to verify it fails differently (route now exists, Services keywords missing)**

Run: `uv run pytest tests/api/routes/test_timeline.py -v`
Expected: FAIL — `TypeError: Services.__init__() got an unexpected keyword argument 'events'`. Proceed to Task 10.

- [ ] **Step 5: Commit**

```bash
git add src/saga/api/routes/timeline.py tests/api/routes/test_timeline.py
git commit -m "feat: add timeline REST routes"
```

---

## Task 10: Wire `Services` + register the router

**Files:**
- Modify: `src/saga/api/dependencies.py` (add two optional fields to `Services`)
- Modify: `src/saga/api/app.py` (construct `EventRecorder` + `TimelineService`; include the router)
- Test: re-run Task 9's route test (now passes).

- [ ] **Step 1: Add optional fields to `Services` in `src/saga/api/dependencies.py`**

In the `if TYPE_CHECKING:` import block add:

```python
    from saga.events import EventRecorder, TimelineService
```

Extend the dataclass (append at the end so existing positional construction keeps working; both default to `None` so existing test fixtures that omit them still work):

```python
@dataclass
class Services:
    """Container for shared, request-scoped services held on ``app.state``."""

    config: AppConfig
    db: Database
    opensearch: ProjectionStore
    minio: BinaryStore
    queue: JobQueue
    search: SearchEngine
    events: EventRecorder | None = None
    timeline: TimelineService | None = None
```

- [ ] **Step 2: Construct and register in `src/saga/api/app.py`**

Add the import near the other `saga` imports:

```python
from saga.events import EventRecorder, TimelineService
```

Add the timeline router to the existing routes import:

```python
from saga.api.routes import doctypes, documents, export, folders, llm, search, timeline
```

In the `lifespan` function, after `search_service = SearchService(...)` and before `app.state.services = Services(...)`, add:

```python
        events = EventRecorder(db, rationale_top_n=cfg.timeline.rationale_top_n)
        timeline_service = TimelineService(db)
```

Update the `Services(...)` construction to pass them:

```python
        app.state.services = Services(
            config=cfg,
            db=db,
            opensearch=opensearch,
            minio=minio,
            queue=queue,
            search=search_service,
            events=events,
            timeline=timeline_service,
        )
```

Register the router alongside the others:

```python
    app.include_router(timeline.router)
```

- [ ] **Step 3: Run the route test to verify it passes**

Run: `uv run pytest tests/api/routes/test_timeline.py -v`
Expected: PASS (both tests).

- [ ] **Step 4: Commit**

```bash
git add src/saga/api/dependencies.py src/saga/api/app.py
git commit -m "feat: wire EventRecorder + TimelineService into Services and register timeline routes"
```

---

## Task 11: Build the recorder into the worker ctx

**Files:**
- Modify: `src/saga/pipeline/worker.py` (`on_startup`: create the recorder, add to ctx)
- Test: covered by Task 12's emission test (the recorder is injected directly there).

- [ ] **Step 1: Add the import and ctx entry in `src/saga/pipeline/worker.py`**

Add the import near the other `saga` imports:

```python
from saga.events import EventRecorder
```

In `on_startup`, after `db = PostgresStore(config.postgres)` (and after `config` is loaded), create the recorder; then register it in ctx next to the other entries:

```python
    events = EventRecorder(db, rationale_top_n=config.timeline.rationale_top_n)
```

```python
    ctx["events"] = events
```

- [ ] **Step 2: Type-check**

Run: `uv run mypy src/saga/pipeline/worker.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/saga/pipeline/worker.py
git commit -m "feat: provide EventRecorder to the worker pipeline context"
```

---

## Task 12: Emit `doc_ingested`, `reclassification`, `placement` in the pipeline

**Files:**
- Modify: `src/saga/pipeline/stages.py` (`classify_doc_type` and `place_in_folder` gain an optional `events` recorder; `place_in_folder` also gains `similar`)
- Modify: `src/saga/pipeline/tasks.py` (capture `similar`, pass `events` + `similar` + `previous_doc_type`, emit `doc_ingested`)
- Test: `tests/pipeline/test_event_emission.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_event_emission.py
from saga.core.models import Event
from saga.events import EventRecorder


class _Sink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def append_event(self, event: Event) -> bool:
        self.events.append(event)
        return True


async def test_recorder_records_reclassification_only_on_change():
    sink = _Sink()
    recorder = EventRecorder(sink)
    await recorder.record_reclassification(
        document_id="d1", from_doc_type=None, to_doc_type="invoice"
    )
    assert [e.event_type for e in sink.events] == ["reclassification"]
    assert sink.events[0].details == {"from_doc_type": None, "to_doc_type": "invoice"}
```

> This locks the recorder contract the stages rely on. The stage wiring itself is verified by `mypy` + the build check (Task 15); a full pipeline integration test is out of scope for Phase 1 (the existing pipeline tests mock the analyzer and do not assert events).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/pipeline/test_event_emission.py -v`
Expected: PASS already (recorder exists from Task 5) — this is a guard test. If it fails, fix the recorder before proceeding. Then continue with the wiring steps below.

- [ ] **Step 3: Add the optional `events` param + emission to `classify_doc_type` in `src/saga/pipeline/stages.py`**

Add `previous_doc_type` and `events` params to the signature:

```python
async def classify_doc_type(
    *,
    document_id: str,
    title: str,
    markdown: str,
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    allow_auto_create: bool,
    previous_doc_type: str | None = None,
    events: EventRecorder | None = None,
    trace_callbacks: list[Any] | None = None,
) -> DocType | None:
```

After the existing `await db.set_document_doc_type(...)` and its `_log.info(...)`, before `return doc_type`, add:

```python
    if events is not None and doc_type.name != previous_doc_type:
        await events.record_reclassification(
            document_id=document_id,
            from_doc_type=previous_doc_type,
            to_doc_type=doc_type.name,
        )
```

Add the import guard at the top `if TYPE_CHECKING:` block:

```python
    from saga.events import EventRecorder
```

- [ ] **Step 4: Add the optional `events` + `similar` params + emission to `place_in_folder`**

Extend the signature:

```python
async def place_in_folder(
    *,
    document_id: str,
    summary: str,
    doc_type: str | None,
    extracted_values: list[ExtractedValue],
    votes: list[FolderVote],
    db: PostgresStore,
    analyzer: DocumentAnalyzer,
    allow_auto_create: bool,
    similar: list[SimilarDocument] | None = None,
    events: EventRecorder | None = None,
    trace_callbacks: list[Any] | None = None,
) -> list[FolderRef]:
```

After the existing `refs = await db.set_document_folders(...)` and its `_log.info("placement_done", ...)`, before `return refs`, add:

```python
    if events is not None:
        await events.record_placement(
            document_id=document_id,
            folders=assignments,
            primary=primary_id,
            similar=similar or [],
            votes=votes,
        )
```

(`SimilarDocument` is already imported under `TYPE_CHECKING` in this file.)

- [ ] **Step 5: Wire the worker ctx through in `src/saga/pipeline/tasks.py`**

Add `EventRecorder` to the `TYPE_CHECKING` imports:

```python
    from saga.events import EventRecorder
```

At the top of `ingest_document`, after the other `ctx[...]` extractions, add:

```python
    events: EventRecorder = ctx["events"]
```

Right after the document is fetched (after `filename = document.filename`), record ingestion and remember the prior doc-type:

```python
        previous_doc_type = document.doc_type
        await events.record_doc_ingested(document_id=document_id)
```

In the `classify_doc_type(...)` call, pass the new args:

```python
            doc_type = await classify_doc_type(
                document_id=document_id,
                title=title,
                markdown=markdown,
                db=db,
                analyzer=analyzer,
                allow_auto_create=llm_config.doctype_classification.allow_auto_create,
                previous_doc_type=previous_doc_type,
                events=events,
                trace_callbacks=callbacks,
            )
```

Capture `similar` from `compute_similarity` (currently discarded as `_`):

```python
            similar, votes = await compute_similarity(
```

In the `place_in_folder(...)` call, pass `similar` and `events`:

```python
                await place_in_folder(
                    document_id=document_id,
                    summary=summary,
                    doc_type=doc_type_name,
                    extracted_values=values,
                    votes=votes,
                    db=db,
                    analyzer=analyzer,
                    allow_auto_create=llm_config.folder_placement.allow_auto_create,
                    similar=similar,
                    events=events,
                    trace_callbacks=callbacks,
                )
```

- [ ] **Step 6: Run the guard test + type-check**

Run: `uv run pytest tests/pipeline/test_event_emission.py -v && uv run mypy src/saga/pipeline`
Expected: test PASS; mypy no errors.

- [ ] **Step 7: Run the existing pipeline tests to confirm no regressions**

Run: `uv run pytest tests/pipeline -v`
Expected: PASS. If any existing pipeline test calls `ingest_document` with a hand-built `ctx` dict, add `ctx["events"] = EventRecorder(<the test's fake db or a stub sink>)` to that fixture. (Use a stub whose `append_event` returns `True`.)

- [ ] **Step 8: Commit**

```bash
git add src/saga/pipeline/stages.py src/saga/pipeline/tasks.py tests/pipeline/test_event_emission.py
git commit -m "feat: emit doc_ingested, reclassification, and placement audit events"
```

---

## Task 13: Emit `move` + `folder_created` from the shared service layer

**Files:**
- Modify: `src/saga/api/service.py` (`set_document_folders`, `add_document_folder`, `remove_document_folder`, `set_primary_folder`, `create_folder`)
- Test: `tests/api/test_service_events.py`

> The service layer is shared by REST and MCP, so emitting here covers both. `assigned_by="llm"` (agent/MCP) maps to actor `agent`; otherwise `user`. The pipeline does **not** go through these service functions (it calls `db.set_document_folders` directly and emits `placement`), so there is no double counting.

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_service_events.py
from types import SimpleNamespace

import pytest

from saga.api import service
from saga.core.models import FolderRef


class _DB:
    async def get_document_folders(self, document_id):
        return [FolderRef(folder_id="old", name="Old", is_primary=True)]

    async def set_document_folders(self, document_id, *, folder_ids, primary_id, assigned_by):
        return [FolderRef(folder_id=fid, name=fid, is_primary=(fid == primary_id)) for fid in folder_ids]


class _Events:
    def __init__(self):
        self.calls = []

    async def record_move(self, **kw):
        self.calls.append(("move", kw))


@pytest.fixture
def services():
    events = _Events()
    return SimpleNamespace(db=_DB(), events=events, opensearch=None, search=None), events


async def test_set_document_folders_records_move(services, monkeypatch):
    svc, events = services
    # reproject touches OpenSearch; stub it out.
    async def _noop_reproject(*a, **k):
        return None

    monkeypatch.setattr(service, "reproject", _noop_reproject)
    await service.set_document_folders(svc, "d1", folder_ids=["new"], primary_id="new", assigned_by="llm")
    assert events.calls[0][0] == "move"
    assert events.calls[0][1]["actor"] == "agent"
    assert events.calls[0][1]["from_folders"] == ["old"]
    assert events.calls[0][1]["to_folders"] == ["new"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_service_events.py -v`
Expected: FAIL — `events.calls` is empty (no emission yet).

- [ ] **Step 3: Add an actor helper + emission in `src/saga/api/service.py`**

Add a small helper near the top (after the constants):

```python
def _actor_from_assigned_by(assigned_by: str) -> str:
    """Map the membership ``assigned_by`` value to a timeline actor."""
    return "agent" if assigned_by == "llm" else "user"
```

In `set_document_folders`, capture the previous folders before the write and emit afterwards:

```python
async def set_document_folders(
    services: Services,
    document_id: str,
    *,
    folder_ids: list[str],
    primary_id: str | None = None,
    assigned_by: str = "user",
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.set_document_folders(
        document_id, folder_ids=folder_ids, primary_id=primary_id, assigned_by=assigned_by
    )
    await reproject(services, document_id)
    if services.events is not None:
        primary = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary,
            actor=_actor_from_assigned_by(assigned_by),
        )
    return refs
```

Apply the same before/after pattern to `add_document_folder`, `remove_document_folder`, and `set_primary_folder` (each already returns the new `refs` and has a `before = await services.db.get_document_folders(document_id)` added at the top; `add_document_folder`/`set_primary_folder` use `_actor_from_assigned_by(assigned_by)` where an `assigned_by` is in scope, else `"user"`).

For `remove_document_folder` and `set_primary_folder` (no `assigned_by` param), use `actor="user"`:

```python
async def remove_document_folder(
    services: Services, document_id: str, folder_id: str
) -> list[FolderRef]:
    before = await services.db.get_document_folders(document_id)
    refs = await services.db.remove_document_folder(document_id, folder_id)
    await reproject(services, document_id)
    if services.events is not None:
        primary = next((r.folder_id for r in refs if r.is_primary), None)
        await services.events.record_move(
            document_id=document_id,
            from_folders=[r.folder_id for r in before],
            to_folders=[r.folder_id for r in refs],
            primary=primary,
            actor="user",
        )
    return refs
```

Find the existing `create_folder` service function and emit a `folder_created` event after the folder is created:

```python
    if services.events is not None:
        await services.events.record_folder_created(
            folder_id=folder.folder_id,
            name=folder.name,
            parent_id=folder.parent_id,
            actor="user",
        )
    return folder
```

Add the `EventRecorder` import under `TYPE_CHECKING` in `service.py` only if needed for annotations (the calls go through `services.events`, so no direct import is required).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_service_events.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing service/API tests for regressions**

Run: `uv run pytest tests/api -v`
Expected: PASS. Existing tests build `Services` without `events`, so `services.events` is `None` and emission is skipped — no breakage.

- [ ] **Step 6: Commit**

```bash
git add src/saga/api/service.py tests/api/test_service_events.py
git commit -m "feat: emit move and folder_created events from the service layer"
```

---

## Task 14: MCP `get_timeline` tool

**Files:**
- Modify: `src/saga/mcp/server.py` (add the read-tool closure + register it)
- Create: `prompts/mcp/get_timeline.md`
- Test: `tests/mcp/test_get_timeline_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_get_timeline_tool.py
from datetime import UTC, datetime
from types import SimpleNamespace

from saga.core.models import Event, EventCategory, EventType
from saga.mcp.server import build_server


class _Timeline:
    async def query(self, q):
        now = datetime(2026, 5, 1, tzinfo=UTC)
        return [
            Event(
                event_id="e1",
                category=EventCategory.AUDIT,
                event_type=EventType.PLACEMENT,
                document_id=q.document_id,
                recorded_at=now,
                actor="pipeline",
                summary="Placed in Finance",
            )
        ]


async def test_get_timeline_tool_registered_and_returns_events(monkeypatch, app_config):
    # app_config: reuse the project's existing MCP test config fixture.
    services = SimpleNamespace(
        config=app_config,
        search=SimpleNamespace(),
        db=SimpleNamespace(),
        timeline=_Timeline(),
    )
    server = build_server(app_config, services)  # type: ignore[arg-type]
    tool = await server.get_tool("get_timeline")
    assert tool is not None
```

> If the project's existing MCP tests use a different harness to invoke tools (e.g. calling the closure directly), mirror that harness here instead of `get_tool`. The essential assertion is that `get_timeline` is registered and returns serialized events.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/mcp/test_get_timeline_tool.py -v`
Expected: FAIL — tool `get_timeline` not found.

- [ ] **Step 3: Add the tool closure in `src/saga/mcp/server.py`**

Add to the `TYPE_CHECKING` imports (for the query type) and the runtime imports:

```python
from saga.core.models import EventCategory, EventType  # add EventCategory, EventType
from saga.events import EventQuery
```

Inside `build_server`, in the read-tools section (after `list_doc_types`), add:

```python
    async def get_timeline(
        document_id: Annotated[str | None, Field(description="Restrict to one document.")] = None,
        folder_id: Annotated[
            str | None, Field(description="Restrict to a folder (and its subtree).")
        ] = None,
        category: Annotated[
            str | None, Field(description="'audit' or 'content'; omit for both.")
        ] = None,
        order_by: Annotated[
            str, Field(description="'recorded_at' (archive time) or 'occurred_at' (event time).")
        ] = "recorded_at",
        limit: Annotated[int, Field(description="Max events to return.")] = 50,
        offset: Annotated[int, Field(description="Pagination offset.")] = 0,
    ) -> dict[str, Any]:
        if services.timeline is None:
            return {"items": [], "limit": limit, "offset": offset}
        categories = (EventCategory(category),) if category else None
        query = EventQuery(
            categories=categories,
            document_id=document_id,
            folder_id=folder_id,
            order_by="occurred_at" if order_by == "occurred_at" else "recorded_at",
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

Register it by adding `get_timeline,` to the `tools: list[Callable[..., Any]] = [...]` list (in the read-tools section).

> `EventType` is imported for symmetry/future use; if `ruff` flags it as unused in Phase 1, drop it from the import — only `EventCategory` and `EventQuery` are required here.

- [ ] **Step 4: Create `prompts/mcp/get_timeline.md`**

```markdown
Return timeline events for the archive: a tagged, chronological log.

There are two kinds of events, selected with `category`:

- `audit` — what happened inside the archive (a document was ingested, classified,
  placed into folders, or moved), including *why* a document was auto-placed (the
  similar documents and folder votes that drove the decision).
- `content` — dates and appointments extracted from document text (e.g. a contract
  start date or a renewal deadline). Sorted by their real-world date.

Filters:
- `document_id` — only events about one document.
- `folder_id` — only events scoped to a folder and its subtree.
- `category` — `audit`, `content`, or omit for both.
- `order_by` — `recorded_at` (when it was archived) or `occurred_at` (the event's own date).
- `limit` / `offset` — pagination.

Use this to answer questions like "what happened to this document?", "why was it filed
here?", or "what is coming up?".
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/mcp/test_get_timeline_tool.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/saga/mcp/server.py prompts/mcp/get_timeline.md tests/mcp/test_get_timeline_tool.py
git commit -m "feat: add get_timeline MCP tool"
```

---

## Task 15: Full verification (lint, types, tests, migration, Docker build)

**Files:** none (verification only).

- [ ] **Step 1: Lint + format**

Run: `uv run ruff check . --fix && uv run ruff format .`
Expected: no remaining errors.

- [ ] **Step 2: Strict type check**

Run: `uv run mypy`
Expected: `Success: no issues found`.

- [ ] **Step 3: Full test suite**

Run: `uv run pytest`
Expected: all pass; coverage on `src/saga/events` and the new store methods ≥ 80%.

- [ ] **Step 4: Verify the migration applies cleanly against a real Postgres**

Run (with the stack's Postgres up, e.g. `docker compose up -d postgres`):
`uv run alembic upgrade head && uv run alembic heads`
Expected: upgrade runs without error; head is `0004_add_events`. Optionally verify downgrade: `uv run alembic downgrade -1 && uv run alembic upgrade head`.

- [ ] **Step 5: Mandatory Docker build check (from `CLAUDE.md`)**

Run (from the workspace root `C:\Projekte\Archiv`): `docker compose build api worker`
Expected: exit code 0.

- [ ] **Step 6: Push the branch and open a PR against `develop`** (only when the user authorizes — see note below)

```bash
git push -u origin feature/timeline-event-log
gh pr create --base develop --fill
```

---

## Self-Review notes (already applied)

- **Spec coverage:** §3 model → Tasks 1–4; §4 audit stream (recorder, emission points, rationale, dedupe, fault tolerance) → Tasks 5, 12, 13; §6 read layer (TimelineService, REST, MCP) → Tasks 6, 9, 10, 14; §7 config/tests/DoD → Tasks 7, 15. Content stream (§5) and recurrence (§3.6/§7 Phase 3) are intentionally **not** in this plan (Phase 2/3).
- **Type consistency:** `append_event(Event) -> bool`, `query_events(... ) -> list[Event]`, `EventRecorder.record_*`, `TimelineService.query(EventQuery)`, and `Services.events/timeline` are referenced identically across tasks.
- **Decisions locked:** `document_id`/`folder_id` are FK-free columns; de-dup is portable check-then-insert backed by a unique index where NULLs are distinct; `Services.events`/`timeline` default to `None` so existing construction sites and test fakes keep working; stages take optional `events` so existing stage tests are unaffected.

## Commit / branch note

Per `saga-core/CLAUDE.md`, commits and pushes happen **only when the user explicitly asks**, on a feature branch (never `main`/`develop`). The per-task commits above are for execution time. The user has indicated they will commit later — an executor should follow the user's direction on when to actually run the commit/push steps.
