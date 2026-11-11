"""Per-user session version: bumping it revokes every existing login token."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_session_version"
down_revision = "0011_one_primary_resume"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("users", "session_version")
