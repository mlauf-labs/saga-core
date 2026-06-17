"""On-read expansion of RECURRING timeline rules into concrete occurrences.

Pure (no DB/LLM access). The TimelineService calls this when a query opts into
recurrence expansion. Occurrences are synthetic, never persisted. See the Phase 3
design (section 4).
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
