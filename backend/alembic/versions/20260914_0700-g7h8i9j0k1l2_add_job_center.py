"""add job center tables and merge alembic heads (#t77)

Revision ID: g7h8i9j0k1l2
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-14 07:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g7h8i9j0k1l2"
down_revision: tuple[str, str] = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_playbooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("filename", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_job_playbooks_tenant_name"),
    )
    op.create_index(op.f("ix_job_playbooks_tenant_id"), "job_playbooks", ["tenant_id"], unique=False)

    op.create_table(
        "job_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("extra_vars", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_job_variables_tenant_name"),
    )
    op.create_index(op.f("ix_job_variables_tenant_id"), "job_variables", ["tenant_id"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=True),
        sa.Column("adhoc_module", sa.String(length=32), nullable=False, server_default="command"),
        sa.Column("adhoc_args", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("target_asset_ids", sa.JSON(), nullable=False),
        sa.Column("extra_var_names", sa.JSON(), nullable=False),
        sa.Column("runas_account_id", sa.Integer(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("check_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["playbook_id"], ["job_playbooks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_jobs_tenant_name"),
    )
    op.create_index(op.f("ix_jobs_tenant_id"), "jobs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_jobs_playbook_id"), "jobs", ["playbook_id"], unique=False)

    op.create_table(
        "job_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(op.f("ix_job_executions_tenant_id"), "job_executions", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_job_executions_job_id"), "job_executions", ["job_id"], unique=False)
    op.create_index(op.f("ix_job_executions_status"), "job_executions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_job_executions_status"), table_name="job_executions")
    op.drop_index(op.f("ix_job_executions_job_id"), table_name="job_executions")
    op.drop_index(op.f("ix_job_executions_tenant_id"), table_name="job_executions")
    op.drop_table("job_executions")
    op.drop_index(op.f("ix_jobs_playbook_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_tenant_id"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_job_variables_tenant_id"), table_name="job_variables")
    op.drop_table("job_variables")
    op.drop_index(op.f("ix_job_playbooks_tenant_id"), table_name="job_playbooks")
    op.drop_table("job_playbooks")
