"""Add pgvector embeddings for resumes and jobs."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002_embeddings"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("resumes", sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True))
    op.add_column("resumes", sa.Column("embedding_model", sa.String(length=128), nullable=True))
    op.add_column("resumes", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("jobs", sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True))
    op.add_column("jobs", sa.Column("embedding_model", sa.String(length=128), nullable=True))
    op.add_column("jobs", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True))

    # HNSW index for future cosine similarity search
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_resumes_embedding_cosine
        ON resumes
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_jobs_embedding_cosine
        ON jobs
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_cosine")
    op.execute("DROP INDEX IF EXISTS ix_resumes_embedding_cosine")

    op.drop_column("jobs", "embedded_at")
    op.drop_column("jobs", "embedding_model")
    op.drop_column("jobs", "embedding")

    op.drop_column("resumes", "embedded_at")
    op.drop_column("resumes", "embedding_model")
    op.drop_column("resumes", "embedding")
