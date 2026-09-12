"""#t77 job center playbooks/jobs/variables; merge alembic heads

Revision ID: b7c8d9e0f1a2
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-12 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: tuple[str, str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ops_playbooks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("filename", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_ops_playbooks_tenant_name"),
    )
    op.create_index(op.f("ix_ops_playbooks_tenant_id"), "ops_playbooks", ["tenant_id"], unique=False)

    op.create_table(
        "ops_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("job_kind", sa.String(length=16), nullable=False),
        sa.Column("playbook_id", sa.Integer(), nullable=True),
        sa.Column("adhoc_module", sa.String(length=32), nullable=True),
        sa.Column("adhoc_command", sa.Text(), nullable=True),
        sa.Column("extra_vars_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("target_asset_ids_json", sa.Text(), nullable=False),
        sa.Column("runas_account_id", sa.Integer(), nullable=False),
        sa.Column("cron_expr", sa.String(length=64), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_ops_jobs_tenant_name"),
    )
    op.create_index(op.f("ix_ops_jobs_tenant_id"), "ops_jobs", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_job_kind"), "ops_jobs", ["job_kind"], unique=False)
    op.create_index(op.f("ix_ops_jobs_playbook_id"), "ops_jobs", ["playbook_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_runas_account_id"), "ops_jobs", ["runas_account_id"], unique=False)
    op.create_index(op.f("ix_ops_jobs_next_run_at"), "ops_jobs", ["next_run_at"], unique=False)

    op.create_table(
        "ops_job_variables",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("job_id", "name", name="uq_ops_job_variables_job_name"),
    )
    op.create_index(
        op.f("ix_ops_job_variables_tenant_id"), "ops_job_variables", ["tenant_id"], unique=False
    )
    op.create_index(op.f("ix_ops_job_variables_job_id"), "ops_job_variables", ["job_id"], unique=False)

    with op.batch_alter_table("automation_job_runs") as batch_op:
        batch_op.add_column(sa.Column("ops_job_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("job_kind", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("runas_account_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("extra_var_keys", sa.String(length=2000), nullable=True))
        batch_op.create_index("ix_automation_job_runs_ops_job_id", ["ops_job_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("automation_job_runs") as batch_op:
        batch_op.drop_index("ix_automation_job_runs_ops_job_id")
        batch_op.drop_column("extra_var_keys")
        batch_op.drop_column("runas_account_id")
        batch_op.drop_column("job_kind")
        batch_op.drop_column("ops_job_id")
    op.drop_index(op.f("ix_ops_job_variables_job_id"), table_name="ops_job_variables")
    op.drop_index(op.f("ix_ops_job_variables_tenant_id"), table_name="ops_job_variables")
    op.drop_table("ops_job_variables")
    op.drop_index(op.f("ix_ops_jobs_next_run_at"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_runas_account_id"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_playbook_id"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_job_kind"), table_name="ops_jobs")
    op.drop_index(op.f("ix_ops_jobs_tenant_id"), table_name="ops_jobs")
    op.drop_table("ops_jobs")
    op.drop_index(op.f("ix_ops_playbooks_tenant_id"), table_name="ops_playbooks")
    op.drop_table("ops_playbooks")
