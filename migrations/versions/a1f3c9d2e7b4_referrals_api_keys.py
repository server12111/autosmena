"""Referral links and reseller API keys."""

import sqlalchemy as sa
from alembic import op

revision = "a1f3c9d2e7b4"
down_revision = "5276eaa9b431"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("referrer_id", sa.BigInteger(), nullable=True))
        batch.create_index("ix_users_referrer_id", ["referrer_id"])
    op.create_table(
        "api_keys",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), primary_key=True, autoincrement=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("last_used_at", sa.BigInteger(), nullable=True),
    )


def downgrade():
    op.drop_table("api_keys")
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_referrer_id")
        batch.drop_column("referrer_id")
