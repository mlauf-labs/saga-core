# tests/events/test_mutations.py
import pytest
from datetime import UTC, datetime
from saga.core.errors import NotFoundError
from saga.core.models import Event, EventCategory, EventType
from saga.events import mutations


class FakeStore:
    def __init__(self, events): self.events = {e.event_id: e for e in events}
    async def get_event(self, eid): return self.events.get(eid)
    async def delete_event(self, eid):
        return self.events.pop(eid, None) is not None
    async def update_event(self, eid, **kw):
        ev = self.events.get(eid)
        if ev is None: return None
        data = ev.model_dump()
        data.update({k: v for k, v in kw.items() if v is not None})
        self.events[eid] = Event(**data)
        return self.events[eid]
    async def merge_events(self, cid, dups):
        if cid not in self.events: return None
        for d in dups:
            if d != cid: self.events.pop(d, None)
        return self.events[cid]


class FakeSink:
    def __init__(self): self.appended = []
    async def append_event(self, event): self.appended.append(event); return True


def _ev(eid, doc="doc1"):
    now = datetime.now(UTC)
    return Event(event_id=eid, category=EventCategory.CONTENT, event_type=EventType.APPOINTMENT,
                 document_id=doc, occurred_at=now, recorded_at=now, actor="agent", summary="x")


@pytest.mark.asyncio
async def test_delete_event_records_audit():
    store, sink = FakeStore([_ev("e1")]), FakeSink()
    await mutations.delete_event(store, sink, "e1")
    assert "e1" not in store.events
    assert sink.appended[0].event_type == EventType.EVENT_CURATED
    assert sink.appended[0].details["action"] == "delete"


@pytest.mark.asyncio
async def test_delete_unknown_raises():
    with pytest.raises(NotFoundError):
        await mutations.delete_event(FakeStore([]), FakeSink(), "nope")


@pytest.mark.asyncio
async def test_merge_event_records_audit():
    store, sink = FakeStore([_ev("c"), _ev("d1")]), FakeSink()
    out = await mutations.merge_events(store, sink, "c", ["d1"])
    assert out.event_id == "c" and "d1" not in store.events
    assert sink.appended[0].details["action"] == "merge"


@pytest.mark.asyncio
async def test_update_event_records_audit_and_returns_updated():
    store, sink = FakeStore([_ev("u1")]), FakeSink()
    updated = await mutations.update_event(store, sink, "u1", summary="new summary")
    assert updated.event_id == "u1"
    assert updated.summary == "new summary"
    assert sink.appended[0].event_type == EventType.EVENT_CURATED
    assert sink.appended[0].details["action"] == "update"
    assert sink.appended[0].details["event_id"] == "u1"


@pytest.mark.asyncio
async def test_update_unknown_raises():
    with pytest.raises(NotFoundError):
        await mutations.update_event(FakeStore([]), FakeSink(), "nope")


@pytest.mark.asyncio
async def test_best_effort_sink_failure_does_not_propagate():
    """A sink that raises must not propagate — the mutation must still succeed."""

    class FailingSink:
        async def append_event(self, event):
            raise RuntimeError("sink down")

    store = FakeStore([_ev("e2")])
    await mutations.delete_event(store, FailingSink(), "e2")
    assert "e2" not in store.events  # mutation succeeded despite sink failure
