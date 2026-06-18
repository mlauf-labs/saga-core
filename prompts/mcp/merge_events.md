---
tool: merge_events
---

Merge several timeline events that describe the same real-world occurrence into one
canonical event. Provide `canonical_event_id` (the event to keep) and `duplicate_event_ids`
(events to fold in and delete). The canonical event keeps its date, type, and summary; the
ids and source documents of the duplicates are recorded on the canonical event for
provenance. Use this only after confirming, via get_document / get_timeline, that the events
truly refer to the same occurrence. Deletion of the duplicates is permanent.
