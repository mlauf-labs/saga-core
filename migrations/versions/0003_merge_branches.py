"""Merge the two 0002 branches (add_filename and add_emoji).

Revision ID: 0003_merge_branches
Revises: 0002_add_filename, 0002_add_emoji
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003_merge_branches"
down_revision: tuple[str, str] = ("0002_add_filename", "0002_add_emoji")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
