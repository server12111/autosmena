"""Persist authenticated webhook events before acknowledging delivery."""

import sqlalchemy as sa
from alembic import op

revision = "5276eaa9b431"
down_revision = "8322f0c1f8e0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("provider", "event_key"),
    )
    op.create_index("ix_webhook_events_status", "webhook_events", ["status"])


def downgrade():
    op.drop_index("ix_webhook_events_status", table_name="webhook_events")
    op.drop_table("webhook_events")
