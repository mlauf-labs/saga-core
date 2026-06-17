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
        validate_rrule("COMPLETELY BROKEN ;; =")


def test_validate_rrule_rejects_unknown_freq() -> None:
    with pytest.raises(ValueError, match="Invalid RRULE"):
        validate_rrule("FREQ=NOPE")
