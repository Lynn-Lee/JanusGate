"""add job center playbooks jobs executions (#t77)

Also merges the parallel k8s-connect head ``f6b0d4c2e815`` into the main line.

Revision ID: b8c9d0e1f2a3
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-08 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_playbooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("playbook_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_job_playbooks_tenant_name"),
    )
    op.create_index(op.f("ix_job_playbooks_tenant_id"), "job_playbooks", ["tenant_id"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=False),
        sa.Column("target_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("check_mode", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["job_playbooks.id"],
            name="fk_jobs_playbook_id_job_playbooks",
            ondelete="RESTRICT",
        ),
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
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("playbook_name", sa.String(length=128), nullable=False),
        sa.Column("check_mode", sa.Boolean(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_job_executions_job_id_jobs",
            ondelete="CASCADE",
        ),
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
    op.drop_index(op.f("ix_job_playbooks_tenant_id"), table_name="job_playbooks")
    op.drop_table("job_playbooks")
