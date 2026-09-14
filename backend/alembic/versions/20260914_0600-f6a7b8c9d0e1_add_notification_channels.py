"""add notification channel types, subscriptions and inbox (#t75)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-14 06:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: tuple[str, str] = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "channel_type",
            sa.String(length=32),
            nullable=False,
            server_default="webhook",
        ),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("credential_encrypted", sa.Text(), nullable=True),
    )

    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.alter_column(
            "notification_rule_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(sa.Column("subscription_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("recipient_user_id", sa.String(length=64), nullable=True))

    op.create_index(
        op.f("ix_notification_deliveries_subscription_id"),
        "notification_deliveries",
        ["subscription_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_recipient_user_id"),
        "notification_deliveries",
        ["recipient_user_id"],
        unique=False,
    )

    op.create_table(
        "system_message_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("event_types_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("webhook_endpoint_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_system_message_subscriptions_tenant_name"),
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_tenant_id"),
        "system_message_subscriptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_webhook_endpoint_id"),
        "system_message_subscriptions",
        ["webhook_endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_recipient_user_id"),
        "system_message_subscriptions",
        ["recipient_user_id"],
        unique=False,
    )

    op.create_table(
        "in_app_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("delivery_id", sa.Integer(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_in_app_messages_tenant_id"), "in_app_messages", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_in_app_messages_user_id"), "in_app_messages", ["user_id"], unique=False)
    op.create_index(op.f("ix_in_app_messages_delivery_id"), "in_app_messages", ["delivery_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_in_app_messages_delivery_id"), table_name="in_app_messages")
    op.drop_index(op.f("ix_in_app_messages_user_id"), table_name="in_app_messages")
    op.drop_index(op.f("ix_in_app_messages_tenant_id"), table_name="in_app_messages")
    op.drop_table("in_app_messages")

    op.drop_index(
        op.f("ix_system_message_subscriptions_recipient_user_id"),
        table_name="system_message_subscriptions",
    )
    op.drop_index(
        op.f("ix_system_message_subscriptions_webhook_endpoint_id"),
        table_name="system_message_subscriptions",
    )
    op.drop_index(
        op.f("ix_system_message_subscriptions_tenant_id"),
        table_name="system_message_subscriptions",
    )
    op.drop_table("system_message_subscriptions")

    op.drop_index(
        op.f("ix_notification_deliveries_recipient_user_id"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_subscription_id"),
        table_name="notification_deliveries",
    )
    with op.batch_alter_table("notification_deliveries") as batch_op:
        batch_op.drop_column("recipient_user_id")
        batch_op.drop_column("subscription_id")
        batch_op.alter_column(
            "notification_rule_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    op.drop_column("webhook_endpoints", "credential_encrypted")
    op.drop_column("webhook_endpoints", "channel_type")
