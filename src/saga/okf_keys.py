"""The OKF frontmatter key contract shared by export and import.

SAGA owns a fixed set of frontmatter keys (the OKF-standard ones it emits, plus everything
prefixed ``saga_``). Every *other* top-level key is free-form document metadata: emitted as a
top-level key on export and captured into ``Document.metadata`` on import. Keeping the rule in
one module guarantees export and import agree, so foreign bundles round-trip losslessly.
"""

from __future__ import annotations

from typing import Any

# The OKF-standard keys SAGA writes in concept frontmatter (export/okf.py:_frontmatter).
RESERVED_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {"type", "title", "description", "tags", "resource", "timestamp"}
)
SAGA_KEY_PREFIX = "saga_"


def is_reserved_key(key: str) -> bool:
    """True if *key* is SAGA-owned (a reserved OKF key or a ``saga_*`` key)."""
    return key in RESERVED_FRONTMATTER_KEYS or key.startswith(SAGA_KEY_PREFIX)


def metadata_from_frontmatter(frontmatter: dict[str, Any]) -> dict[str, str]:
    """Return the free-form metadata in *frontmatter*: every non-reserved key, value→str."""
    return {k: str(v) for k, v in frontmatter.items() if not is_reserved_key(k)}
