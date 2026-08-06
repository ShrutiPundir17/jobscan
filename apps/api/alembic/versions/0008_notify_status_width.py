"""Widen notification delivery status columns.

Long error strings like failed:[Errno 101] Network is unreachable
exceeded VARCHAR(32) and crashed the test-alert path.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_notify_status_width"
down_revision = "0007_resume_tailoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "email_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.alter_column(
        "notifications",
        "whatsapp_status",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "notifications",
        "email_status",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
    op.alter_column(
        "notifications",
        "whatsapp_status",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
