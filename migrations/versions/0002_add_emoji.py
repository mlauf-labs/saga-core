"""Add emoji column to folders and doc_types.

Revision ID: 0002_add_emoji
Revises: 0001_initial
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_add_emoji"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("doc_types", sa.Column("emoji", sa.String(length=10), nullable=True))
    op.add_column("folders", sa.Column("emoji", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("folders", "emoji")
    op.drop_column("doc_types", "emoji")
