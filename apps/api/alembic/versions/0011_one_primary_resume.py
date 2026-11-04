"""At most one primary resume per user, enforced by the database."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_one_primary_resume"
down_revision = "0010_auto_apply"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Normalize existing data first: keep the newest primary per user...
    op.execute(
        """
        UPDATE resumes r SET is_primary = false
        WHERE r.is_primary AND r.id <> (
            SELECT r2.id FROM resumes r2
            WHERE r2.user_id = r.user_id AND r2.is_primary
            ORDER BY r2.updated_at DESC, r2.created_at DESC, r2.id
            LIMIT 1
        )
        """
    )
    # ...and give users who have resumes but no primary their best candidate
    # (embedded first, then newest) so matching keeps working.
    op.execute(
        """
        UPDATE resumes r SET is_primary = true
        WHERE r.id IN (
            SELECT DISTINCT ON (r2.user_id) r2.id FROM resumes r2
            WHERE NOT EXISTS (
                SELECT 1 FROM resumes p WHERE p.user_id = r2.user_id AND p.is_primary
            )
            ORDER BY r2.user_id, (r2.embedded_at IS NOT NULL) DESC, r2.created_at DESC, r2.id
        )
        """
    )
    op.create_index(
        "uq_resumes_one_primary_per_user",
        "resumes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )


def downgrade() -> None:
    op.drop_index("uq_resumes_one_primary_per_user", table_name="resumes")
