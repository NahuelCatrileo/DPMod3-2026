"""US-C1 · Subtarea 1.1 — tabla publication

Revision ID: 0001
Revises:
Create Date: 2026-09-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publication",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("content_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("schedule_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("youtube_video_id", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_publication_content_id", "publication", ["content_id"])
    op.create_index("ix_publication_state", "publication", ["state"])
    op.create_index("ix_publication_schedule_at", "publication", ["schedule_at"])
    # La tabla apscheduler_jobs la crea APScheduler solo al arrancar.


def downgrade() -> None:
    op.drop_index("ix_publication_schedule_at", table_name="publication")
    op.drop_index("ix_publication_state", table_name="publication")
    op.drop_index("ix_publication_content_id", table_name="publication")
    op.drop_table("publication")
