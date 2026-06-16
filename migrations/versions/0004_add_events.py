"""Add the events table (timeline/audit log).

Revision ID: 0004_add_events
Revises: 0003_merge_branches
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004_add_events"
down_revision: str | None = "0003_merge_branches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("folder_id", sa.String(length=32), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
    )
    op.create_index("ix_events_document_id", "events", ["document_id"])
    op.create_index("ix_events_folder_id", "events", ["folder_id"])
    op.create_index("ix_events_category", "events", ["category"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_occurred_at", "events", ["occurred_at"])
    op.create_index("ix_events_recorded_at", "events", ["recorded_at"])
    op.create_index("uq_events_dedupe_key", "events", ["dedupe_key"], unique=True)


def downgrade() -> None:
    op.drop_table("events")
