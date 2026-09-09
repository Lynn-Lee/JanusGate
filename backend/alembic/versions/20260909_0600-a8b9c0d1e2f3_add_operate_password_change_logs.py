"""add operate_logs and password_change_logs for #t78

Revision ID: a8b9c0d1e2f3
Revises: f7b8c9d0e1f2
Create Date: 2026-09-09 06:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operate_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_username", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operate_logs_tenant_id"), "operate_logs", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_operate_logs_audit_event_id"), "operate_logs", ["audit_event_id"], unique=False
    )
    op.create_index(
        "ix_operate_logs_tenant_occurred_id",
        "operate_logs",
        ["tenant_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_table(
        "password_change_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_password_change_logs_tenant_id"),
        "password_change_logs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_password_change_logs_audit_event_id"),
        "password_change_logs",
        ["audit_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_password_change_logs_tenant_occurred_id",
        "password_change_logs",
        ["tenant_id", "occurred_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_password_change_logs_tenant_occurred_id", table_name="password_change_logs")
    op.drop_index(op.f("ix_password_change_logs_audit_event_id"), table_name="password_change_logs")
    op.drop_index(op.f("ix_password_change_logs_tenant_id"), table_name="password_change_logs")
    op.drop_table("password_change_logs")
    op.drop_index("ix_operate_logs_tenant_occurred_id", table_name="operate_logs")
    op.drop_index(op.f("ix_operate_logs_audit_event_id"), table_name="operate_logs")
    op.drop_index(op.f("ix_operate_logs_tenant_id"), table_name="operate_logs")
    op.drop_table("operate_logs")
