"""add job center definitions and run metadata for #t77

Revision ID: a7b8c9d0e1f2
Revises: e5f6a7b8c9d0
Create Date: 2026-09-10 04:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_definitions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("extra_variables", sa.JSON(), nullable=False),
        sa.Column("cron_expression", sa.String(length=64), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("run_as_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_job_definitions_tenant_id", "job_definitions", ["tenant_id"])
    op.create_index("ix_job_definitions_job_type", "job_definitions", ["job_type"])
    op.add_column("automation_job_runs", sa.Column("job_definition_id", sa.String(length=64), nullable=True))
    op.add_column("automation_job_runs", sa.Column("extra_variables", sa.JSON(), nullable=True))
    op.add_column("automation_job_runs", sa.Column("run_as_user_id", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_automation_job_runs_job_definition_id",
        "automation_job_runs",
        ["job_definition_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_automation_job_runs_job_definition_id", table_name="automation_job_runs")
    op.drop_column("automation_job_runs", "run_as_user_id")
    op.drop_column("automation_job_runs", "extra_variables")
    op.drop_column("automation_job_runs", "job_definition_id")
    op.drop_index("ix_job_definitions_job_type", table_name="job_definitions")
    op.drop_index("ix_job_definitions_tenant_id", table_name="job_definitions")
    op.drop_table("job_definitions")
