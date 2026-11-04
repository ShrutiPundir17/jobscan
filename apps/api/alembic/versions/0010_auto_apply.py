"""Auto-apply: ATS target + review plan on applications, resume bytes, LinkedIn URL."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_auto_apply"
down_revision = "0009_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("linkedin_url", sa.String(length=512), nullable=True))
    # Resume bytes live in Postgres so Celery workers on other hosts can upload them.
    op.add_column("resumes", sa.Column("file_data", sa.LargeBinary(), nullable=True))

    op.add_column("applications", sa.Column("auto_apply_state", sa.String(length=32), nullable=True))
    op.add_column("applications", sa.Column("auto_apply_provider", sa.String(length=32), nullable=True))
    op.add_column("applications", sa.Column("auto_apply_url", sa.String(length=2048), nullable=True))
    op.add_column("applications", sa.Column("auto_apply_plan", JSONB(), nullable=True))
    op.add_column("applications", sa.Column("auto_apply_error", sa.Text(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("auto_apply_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_applications_auto_apply_state", "applications", ["auto_apply_state"]
    )


def downgrade() -> None:
    op.drop_index("ix_applications_auto_apply_state", table_name="applications")
    for col in (
        "auto_apply_updated_at",
        "auto_apply_error",
        "auto_apply_plan",
        "auto_apply_url",
        "auto_apply_provider",
        "auto_apply_state",
    ):
        op.drop_column("applications", col)
    op.drop_column("resumes", "file_data")
    op.drop_column("users", "linkedin_url")
