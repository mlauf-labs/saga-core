Return upcoming events for the archive — a date-sorted agenda of what is coming up.

Combines future content events (appointments, deadlines, upcoming dated facts) with
**recurring obligations expanded into their concrete next occurrences** within a bounded
horizon. Recurring rules are expanded on the fly; you receive the individual occurrence
dates, not the abstract rule.

Filters:
- `folder_id` — restrict to a folder and its subtree.
- `limit` / `offset` — pagination.

Events are sorted ascending by their real-world date (`occurred_at`). Use this to answer
"what's coming up?", "what are my next deadlines?", or "what renews soon?".
