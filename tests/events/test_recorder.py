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


async def test_record_placement_builds_audit_event_with_rationale() -> None:
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
    assert event.dedupe_key is None  # placement is recorded on change, not content-deduped
    assert [s["title"] for s in event.details["similar"]] == ["KFZ-Police 2025", "Hausrat 2024"]
    assert "KFZ-Police 2025" in event.summary


async def test_recorder_never_raises_when_sink_fails() -> None:
    recorder = EventRecorder(_Sink(fail=True))
    await recorder.record_doc_ingested(document_id="d1")  # must NOT raise
