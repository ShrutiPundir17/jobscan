"""Persistent match dismissal and follow-up reminders on applications."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_dismiss_and_follow_up"
down_revision = "0012_session_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("applications", sa.Column("follow_up_notified_at", sa.DateTime(timezone=True), nullable=True))
    # The reminder sweep looks up due, not-yet-sent reminders.
    op.create_index(
        "ix_applications_follow_up_due",
        "applications",
        ["follow_up_at"],
        postgresql_where=sa.text("follow_up_at IS NOT NULL AND follow_up_notified_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_applications_follow_up_due", table_name="applications")
    op.drop_column("applications", "follow_up_notified_at")
    op.drop_column("applications", "follow_up_at")
    op.drop_column("applications", "dismissed_at")
