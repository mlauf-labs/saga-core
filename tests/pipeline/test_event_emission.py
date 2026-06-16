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
