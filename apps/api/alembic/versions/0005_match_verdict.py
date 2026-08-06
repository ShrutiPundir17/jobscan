"""Add match_verdict on applications; backfill from match_score."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_match_verdict"
down_revision: Union[str, None] = "0004_job_dedupe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("match_verdict", sa.String(length=32), nullable=True),
    )
    # Backfill from existing scores (same bands as Stage 2 calibration).
    op.execute(
        """
        UPDATE applications
        SET match_verdict = CASE
            WHEN match_score IS NULL THEN NULL
            WHEN match_score >= 85 THEN 'strong'
            WHEN match_score >= 70 THEN 'good'
            WHEN match_score >= 50 THEN 'partial'
            ELSE 'weak'
        END
        WHERE match_verdict IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("applications", "match_verdict")
