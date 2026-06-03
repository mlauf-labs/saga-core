"""Backup/export script (``docstore-backup``). Implemented in Phase 7 (FR-28..31).

Pages through the REST export endpoint and writes, per document, into a directory
derived from ``folder_structure[0]``: the original binary, the converted ``.md``
text, and a ``*.metadata.json`` sidecar.
"""

from __future__ import annotations


def main() -> None:
    raise NotImplementedError("Backup script is implemented in Phase 7.")


if __name__ == "__main__":
    main()
