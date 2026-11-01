"""Store business preferences in the database instead of environment variables."""

import sqlalchemy as sa
from alembic import op

revision = "8322f0c1f8e0"
down_revision = "cd52fdf5a6c0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("app_settings")
