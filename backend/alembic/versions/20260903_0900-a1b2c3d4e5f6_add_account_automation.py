"""add account automation models (#t73)

Revision ID: a1b2c3d4e5f6
Revises: f8a1d2c3b4e5
Create Date: 2026-09-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8a1d2c3b4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default="ssh"),
        sa.Column("privileged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("shell", sa.String(length=120), nullable=True),
        sa.Column("home_dir", sa.String(length=240), nullable=True),
        sa.Column("groups_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
        sa.Column("team_id", sa.String(length=64), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_account_templates_tenant_name"),
    )
    op.create_index(op.f("ix_account_templates_tenant_id"), "account_templates", ["tenant_id"])
    op.create_index(
        op.f("ix_account_templates_organization_id"), "account_templates", ["organization_id"]
    )
    op.create_index(op.f("ix_account_templates_team_id"), "account_templates", ["team_id"])
    op.create_index(op.f("ix_account_templates_project_id"), "account_templates", ["project_id"])

    op.create_table(
        "account_risks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("risk_type", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column("detail", sa.String(length=480), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("source_job_type", sa.String(length=64), nullable=True),
        sa.Column("source_message_id", sa.String(length=120), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "asset_id",
            "username",
            "risk_type",
            name="uq_account_risks_asset_user_type",
        ),
    )
    op.create_index(op.f("ix_account_risks_tenant_id"), "account_risks", ["tenant_id"])
    op.create_index(op.f("ix_account_risks_asset_id"), "account_risks", ["asset_id"])
    op.create_index(op.f("ix_account_risks_account_id"), "account_risks", ["account_id"])
    op.create_index(op.f("ix_account_risks_risk_type"), "account_risks", ["risk_type"])
    op.create_index(op.f("ix_account_risks_status"), "account_risks", ["status"])

    op.create_table(
        "account_automation_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.String(length=120), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.String(length=480), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
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
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        op.f("ix_account_automation_runs_tenant_id"), "account_automation_runs", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_account_automation_runs_job_type"), "account_automation_runs", ["job_type"]
    )
    op.create_index(
        op.f("ix_account_automation_runs_status"), "account_automation_runs", ["status"]
    )
    op.create_index(
        op.f("ix_account_automation_runs_account_id"), "account_automation_runs", ["account_id"]
    )
    op.create_index(
        op.f("ix_account_automation_runs_asset_id"), "account_automation_runs", ["asset_id"]
    )

    op.create_table(
        "account_backups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("secret_id_ref", sa.String(length=120), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("source_message_id", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_account_backups_tenant_id"), "account_backups", ["tenant_id"])
    op.create_index(op.f("ix_account_backups_account_id"), "account_backups", ["account_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_account_backups_account_id"), table_name="account_backups")
    op.drop_index(op.f("ix_account_backups_tenant_id"), table_name="account_backups")
    op.drop_table("account_backups")

    op.drop_index(op.f("ix_account_automation_runs_asset_id"), table_name="account_automation_runs")
    op.drop_index(
        op.f("ix_account_automation_runs_account_id"), table_name="account_automation_runs"
    )
    op.drop_index(op.f("ix_account_automation_runs_status"), table_name="account_automation_runs")
    op.drop_index(op.f("ix_account_automation_runs_job_type"), table_name="account_automation_runs")
    op.drop_index(
        op.f("ix_account_automation_runs_tenant_id"), table_name="account_automation_runs"
    )
    op.drop_table("account_automation_runs")

    op.drop_index(op.f("ix_account_risks_status"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_risk_type"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_account_id"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_asset_id"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_tenant_id"), table_name="account_risks")
    op.drop_table("account_risks")

    op.drop_index(op.f("ix_account_templates_project_id"), table_name="account_templates")
    op.drop_index(op.f("ix_account_templates_team_id"), table_name="account_templates")
    op.drop_index(op.f("ix_account_templates_organization_id"), table_name="account_templates")
    op.drop_index(op.f("ix_account_templates_tenant_id"), table_name="account_templates")
    op.drop_table("account_templates")
