"""add activity_logs and online_user_sessions for #t78

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-09-09 18:20:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("actor_username", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=120), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_activity_logs_tenant_id"), "activity_logs", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_activity_logs_audit_event_id"), "activity_logs", ["audit_event_id"], unique=False
    )
    op.create_index(
        "ix_activity_logs_tenant_occurred_id",
        "activity_logs",
        ["tenant_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_activity_logs_tenant_resource",
        "activity_logs",
        ["tenant_id", "resource_type", "resource_id"],
        unique=False,
    )
    op.create_table(
        "online_user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("client_ip", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False),
        sa.Column("ended_audit_event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_online_user_sessions_tenant_id"),
        "online_user_sessions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_online_user_sessions_user_id"),
        "online_user_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_online_user_sessions_audit_event_id"),
        "online_user_sessions",
        ["audit_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_online_user_sessions_tenant_status_id",
        "online_user_sessions",
        ["tenant_id", "status", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_online_user_sessions_tenant_status_id", table_name="online_user_sessions")
    op.drop_index(op.f("ix_online_user_sessions_audit_event_id"), table_name="online_user_sessions")
    op.drop_index(op.f("ix_online_user_sessions_user_id"), table_name="online_user_sessions")
    op.drop_index(op.f("ix_online_user_sessions_tenant_id"), table_name="online_user_sessions")
    op.drop_table("online_user_sessions")
    op.drop_index("ix_activity_logs_tenant_resource", table_name="activity_logs")
    op.drop_index("ix_activity_logs_tenant_occurred_id", table_name="activity_logs")
    op.drop_index(op.f("ix_activity_logs_audit_event_id"), table_name="activity_logs")
    op.drop_index(op.f("ix_activity_logs_tenant_id"), table_name="activity_logs")
    op.drop_table("activity_logs")
