"""add ops playbooks, jobs, variables and executions (#t77)

Revision ID: a8b9c0d1e2f3
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-13 09:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: tuple[str, str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_playbooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("relative_path", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ops_playbooks_tenant_name"),
    )
    op.create_index(op.f("ix_ops_playbooks_tenant_id"), "ops_playbooks", ["tenant_id"], unique=False)

    op.create_table(
        "ops_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=True),
        sa.Column("adhoc_module", sa.String(length=32), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("target_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("extra_vars_json", sa.Text(), nullable=False),
        sa.Column("runas_account_id", sa.Integer(), nullable=False),
        sa.Column("cron_expr", sa.String(length=64), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_mode", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(
            ["playbook_id"],
            ["ops_playbooks.id"],
            name="fk_ops_jobs_playbook_id_ops_playbooks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["runas_account_id"],
            ["accounts.id"],
            name="fk_ops_jobs_runas_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ops_jobs_tenant_name"),
    )
    op.create_index(op.f("ix_ops_jobs_tenant_id"), "ops_jobs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_playbook_id"), "ops_jobs", ["playbook_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_runas_account_id"), "ops_jobs", ["runas_account_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_next_run_at"), "ops_jobs", ["next_run_at"], unique=False)

    op.create_table(
        "ops_job_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("default_value", sa.Text(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ops_jobs.id"],
            name="fk_ops_job_variables_job_id_ops_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "name", name="uq_ops_job_variables_job_name"),
    )
    op.create_index(op.f("ix_ops_job_variables_job_id"), "ops_job_variables", ["job_id"], unique=False)

    op.create_table(
        "ops_job_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.String(length=120), nullable=True),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("playbook_name", sa.String(length=255), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("adhoc_module", sa.String(length=32), nullable=True),
        sa.Column("target_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("extra_vars_json", sa.Text(), nullable=False),
        sa.Column("runas_account_id", sa.Integer(), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("check_mode", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ops_jobs.id"],
            name="fk_ops_job_executions_job_id_ops_jobs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ops_job_executions_tenant_id"), "ops_job_executions", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ops_job_executions_job_id"), "ops_job_executions", ["job_id"], unique=False)
    op.create_index(op.f("ix_ops_job_executions_message_id"), "ops_job_executions", ["message_id"], unique=False)
    op.create_index(
        op.f("ix_ops_job_executions_runas_account_id"),
        "ops_job_executions",
        ["runas_account_id"],
        unique=False,
    )
    op.create_index(op.f("ix_ops_job_executions_status"), "ops_job_executions", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ops_job_executions_status"), table_name="ops_job_executions")
    op.drop_index(op.f("ix_ops_job_executions_runas_account_id"), table_name="ops_job_executions")
    op.drop_index(op.f("ix_ops_job_executions_message_id"), table_name="ops_job_executions")
    op.drop_index(op.f("ix_ops_job_executions_job_id"), table_name="ops_job_executions")
    op.drop_index(op.f("ix_ops_job_executions_tenant_id"), table_name="ops_job_executions")
    op.drop_table("ops_job_executions")
    op.drop_index(op.f("ix_ops_job_variables_job_id"), table_name="ops_job_variables")
    op.drop_table("ops_job_variables")
    op.drop_index(op.f("ix_ops_jobs_next_run_at"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_runas_account_id"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_playbook_id"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_tenant_id"), table_name="ops_jobs")
    op.drop_table("ops_jobs")
    op.drop_index(op.f("ix_ops_playbooks_tenant_id"), table_name="ops_playbooks")
    op.drop_table("ops_playbooks")
