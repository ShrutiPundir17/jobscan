"""Split match pitch from tailored resume; add tailored_bullets JSON."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_resume_tailoring"
down_revision: Union[str, None] = "0006_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("match_pitch", sa.Text(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("tailored_bullets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Existing Stage 2 pitch lived in tailored_resume_text — move it.
    op.execute(
        """
        UPDATE applications
        SET match_pitch = tailored_resume_text
        WHERE match_pitch IS NULL
          AND tailored_resume_text IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE applications
        SET tailored_resume_text = NULL
        WHERE tailored_bullets IS NULL
          AND match_pitch IS NOT NULL
          AND tailored_resume_text = match_pitch
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE applications
        SET tailored_resume_text = COALESCE(tailored_resume_text, match_pitch)
        """
    )
    op.drop_column("applications", "tailored_bullets")
    op.drop_column("applications", "match_pitch")
