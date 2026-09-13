"""add account templates, verify fields, account risks (#t73)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-07 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default="ssh"),
        sa.Column("default_username", sa.String(length=100), nullable=False),
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
    op.create_index(op.f("ix_account_templates_tenant_id"), "account_templates", ["tenant_id"], unique=False)

    op.add_column("accounts", sa.Column("template_id", sa.Integer(), nullable=True))
    op.add_column(
        "accounts",
        sa.Column(
            "verify_status",
            sa.String(length=20),
            nullable=False,
            server_default="unverified",
        ),
    )
    op.add_column("accounts", sa.Column("last_verify_message_id", sa.String(length=120), nullable=True))
    op.add_column("accounts", sa.Column("last_verify_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_accounts_template_id"), "accounts", ["template_id"], unique=False)
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.create_foreign_key(
            "fk_accounts_template_id_account_templates",
            "account_templates",
            ["template_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "account_risks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("risk_type", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False, server_default="校验失败"),
        sa.Column("job_message_id", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_account_risks_tenant_id"), "account_risks", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_account_risks_account_id"), "account_risks", ["account_id"], unique=False)
    op.create_index(op.f("ix_account_risks_risk_type"), "account_risks", ["risk_type"], unique=False)
    op.create_index(op.f("ix_account_risks_job_message_id"), "account_risks", ["job_message_id"], unique=False)

    # one-line user reason for automation runs (no stack)
    op.add_column("automation_job_runs", sa.Column("reason", sa.String(length=240), nullable=True))


def downgrade() -> None:
    op.drop_column("automation_job_runs", "reason")
    op.drop_index(op.f("ix_account_risks_job_message_id"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_risk_type"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_account_id"), table_name="account_risks")
    op.drop_index(op.f("ix_account_risks_tenant_id"), table_name="account_risks")
    op.drop_table("account_risks")
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint("fk_accounts_template_id_account_templates", type_="foreignkey")
    op.drop_index(op.f("ix_accounts_template_id"), table_name="accounts")
    op.drop_column("accounts", "last_verify_at")
    op.drop_column("accounts", "last_verify_message_id")
    op.drop_column("accounts", "verify_status")
    op.drop_column("accounts", "template_id")
    op.drop_index(op.f("ix_account_templates_tenant_id"), table_name="account_templates")
    op.drop_table("account_templates")
