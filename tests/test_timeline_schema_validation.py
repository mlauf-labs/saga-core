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
