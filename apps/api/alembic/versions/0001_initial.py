"""Initial schema: users, resumes, jobs, applications."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

application_status = postgresql.ENUM(
    "discovered",
    "matched",
    "pending_review",
    "applying",
    "applied",
    "interviewing",
    "offered",
    "rejected",
    "failed",
    "withdrawn",
    name="application_status",
    create_type=False,
)


def upgrade() -> None:
    application_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("auto_apply_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("min_match_score", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("employment_type", sa.String(length=100), nullable=True),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
    op.create_index(op.f("ix_jobs_company"), "jobs", ["company"], unique=False)
    op.create_index(op.f("ix_jobs_source"), "jobs", ["source"], unique=False)

    op.create_table(
        "resumes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=True),
        sa.Column("storage_path", sa.String(length=1024), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("parsed_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resumes_user_id"), "resumes", ["user_id"], unique=False)

    op.create_table(
        "applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            application_status,
            nullable=False,
            server_default="discovered",
        ),
        sa.Column("match_score", sa.Integer(), nullable=True),
        sa.Column("match_reasoning", sa.Text(), nullable=True),
        sa.Column("skill_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tailored_resume_text", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
    )
    op.create_index(op.f("ix_applications_job_id"), "applications", ["job_id"], unique=False)
    op.create_index(op.f("ix_applications_resume_id"), "applications", ["resume_id"], unique=False)
    op.create_index(op.f("ix_applications_status"), "applications", ["status"], unique=False)
    op.create_index(op.f("ix_applications_user_id"), "applications", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_applications_user_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_status"), table_name="applications")
    op.drop_index(op.f("ix_applications_resume_id"), table_name="applications")
    op.drop_index(op.f("ix_applications_job_id"), table_name="applications")
    op.drop_table("applications")

    op.drop_index(op.f("ix_resumes_user_id"), table_name="resumes")
    op.drop_table("resumes")

    op.drop_index(op.f("ix_jobs_source"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_company"), table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    application_status.drop(op.get_bind(), checkfirst=True)
