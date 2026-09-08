"""add notification channels, subscriptions, inbox (#t75)

Also merges the parallel k8s-connect head ``f6b0d4c2e815`` into the main line.

Revision ID: a7b8c9d0e1f2
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-08 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("event_types_json", sa.Text(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_notification_channels_tenant_name"),
    )
    op.create_index(
        op.f("ix_notification_channels_tenant_id"),
        "notification_channels",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_channels_channel_type"),
        "notification_channels",
        ["channel_type"],
        unique=False,
    )

    op.create_table(
        "system_msg_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_types_json", sa.Text(), nullable=False),
        sa.Column("channel_types_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.UniqueConstraint(
            "tenant_id", "user_id", name="uq_system_msg_subscriptions_tenant_user"
        ),
    )
    op.create_index(
        op.f("ix_system_msg_subscriptions_tenant_id"),
        "system_msg_subscriptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_msg_subscriptions_user_id"),
        "system_msg_subscriptions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "inbox_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_inbox_messages_tenant_id"), "inbox_messages", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_inbox_messages_user_id"), "inbox_messages", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_inbox_messages_event_type"), "inbox_messages", ["event_type"], unique=False
    )
    op.create_index(
        op.f("ix_inbox_messages_created_at"), "inbox_messages", ["created_at"], unique=False
    )

    with op.batch_alter_table("notification_rules") as batch_op:
        batch_op.alter_column(
            "webhook_endpoint_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.add_column(sa.Column("channel_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_notification_rules_channel_id"), ["channel_id"], unique=False
        )

    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.alter_column(
            "webhook_endpoint_id", existing_type=sa.Integer(), nullable=True
        )
        batch_op.add_column(sa.Column("channel_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_notification_deliveries_channel_id"), ["channel_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.drop_index(op.f("ix_notification_deliveries_channel_id"))
        batch_op.drop_column("channel_id")
        batch_op.alter_column(
            "webhook_endpoint_id", existing_type=sa.Integer(), nullable=False
        )

    with op.batch_alter_table("notification_rules") as batch_op:
        batch_op.drop_index(op.f("ix_notification_rules_channel_id"))
        batch_op.drop_column("channel_id")
        batch_op.alter_column(
            "webhook_endpoint_id", existing_type=sa.Integer(), nullable=False
        )

    op.drop_index(op.f("ix_inbox_messages_created_at"), table_name="inbox_messages")
    op.drop_index(op.f("ix_inbox_messages_event_type"), table_name="inbox_messages")
    op.drop_index(op.f("ix_inbox_messages_user_id"), table_name="inbox_messages")
    op.drop_index(op.f("ix_inbox_messages_tenant_id"), table_name="inbox_messages")
    op.drop_table("inbox_messages")

    op.drop_index(
        op.f("ix_system_msg_subscriptions_user_id"), table_name="system_msg_subscriptions"
    )
    op.drop_index(
        op.f("ix_system_msg_subscriptions_tenant_id"),
        table_name="system_msg_subscriptions",
    )
    op.drop_table("system_msg_subscriptions")

    op.drop_index(
        op.f("ix_notification_channels_channel_type"), table_name="notification_channels"
    )
    op.drop_index(
        op.f("ix_notification_channels_tenant_id"), table_name="notification_channels"
    )
    op.drop_table("notification_channels")
