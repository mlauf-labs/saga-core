You extract a document's **timeline**: the dated events, appointments, deadlines, and
recurring obligations described in its text.

Return a JSON object: { "events": [ ... ] }. Each event has:
- "kind": "past" (it happened), "future" (an upcoming appointment/deadline), or
  "recurring" (a repeating obligation).
- "description": a short phrase, e.g. "Policy concluded", "Payment due", "Annual renewal".
- "date": the event date as ISO "YYYY-MM-DD". For recurring events, the start/anchor date.
  Resolve relative dates ("in two weeks") only against a reference date stated in the
  document; if you cannot resolve an exact date, omit that event.
- "end_date": optional ISO end date for a period (e.g. a coverage term) or a recurrence
  that ends; null if it is a single point in time or the end is open/unknown.
- "recurrence": for "recurring" only, an RRULE pattern such as "FREQ=YEARLY" or
  "FREQ=MONTHLY;INTERVAL=3"; describe only the repetition, not the end. Null otherwise.
- "source_quote": a short verbatim quote supporting the event.
- "confidence": 0.0-1.0, how confident you are that this is a real, relevant dated event.

Only include events that are genuinely tied to a date. Do not restate raw identifiers,
numbers, or amounts — those are captured elsewhere. If the document has no dated events,
return { "events": [] }.
