"""Add documents.filename column (backfill from title).

Revision ID: 0002_add_filename
Revises: 0001_initial
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_add_filename"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add as nullable first so the backfill can run before adding the NOT NULL constraint.
    op.add_column("documents", sa.Column("filename", sa.String(length=1024), nullable=True))
    op.execute("UPDATE documents SET filename = title WHERE filename IS NULL")
    op.alter_column("documents", "filename", nullable=False)


def downgrade() -> None:
    op.drop_column("documents", "filename")
