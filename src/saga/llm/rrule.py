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
