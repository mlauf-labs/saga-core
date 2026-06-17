from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType


def test_event_defaults_and_roundtrip() -> None:
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
