from __future__ import annotations

from datetime import UTC, datetime

from saga.core.models import Event, EventCategory, EventType
from saga.events.recurrence import expand_recurrences


def _rule(
    recurrence: str | None, *, anchor: str = "2026-05-01", end_date: str | None = None
) -> Event:
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
