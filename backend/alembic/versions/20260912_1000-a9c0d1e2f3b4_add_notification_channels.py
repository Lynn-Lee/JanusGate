"""#t75 notification channels, subscriptions, inbox; merge alembic heads

Revision ID: a9c0d1e2f3b4
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-12 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c0d1e2f3b4"
down_revision: tuple[str, str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("webhook_endpoints") as batch_op:
        batch_op.add_column(
            sa.Column(
                "channel_type",
                sa.String(length=32),
                nullable=False,
                server_default="webhook",
            )
        )
        batch_op.add_column(sa.Column("credential_encrypted", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("recipient", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("auth_username", sa.String(length=120), nullable=True))

    op.create_table(
        "system_message_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("webhook_endpoint_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "event_type",
            "webhook_endpoint_id",
            "user_id",
            name="uq_system_message_subscriptions_target",
        ),
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_tenant_id"),
        "system_message_subscriptions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_event_type"),
        "system_message_subscriptions",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_webhook_endpoint_id"),
        "system_message_subscriptions",
        ["webhook_endpoint_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_message_subscriptions_user_id"),
        "system_message_subscriptions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "in_app_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("delivery_id", sa.Integer(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_in_app_messages_tenant_id"), "in_app_messages", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_in_app_messages_user_id"), "in_app_messages", ["user_id"], unique=False)
    op.create_index(op.f("ix_in_app_messages_event_type"), "in_app_messages", ["event_type"], unique=False)
    op.create_index(op.f("ix_in_app_messages_delivery_id"), "in_app_messages", ["delivery_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_in_app_messages_delivery_id"), table_name="in_app_messages")
    op.drop_index(op.f("ix_in_app_messages_event_type"), table_name="in_app_messages")
    op.drop_index(op.f("ix_in_app_messages_user_id"), table_name="in_app_messages")
    op.drop_index(op.f("ix_in_app_messages_tenant_id"), table_name="in_app_messages")
    op.drop_table("in_app_messages")
    op.drop_index(op.f("ix_system_message_subscriptions_user_id"), table_name="system_message_subscriptions")
    op.drop_index(
        op.f("ix_system_message_subscriptions_webhook_endpoint_id"),
        table_name="system_message_subscriptions",
    )
    op.drop_index(op.f("ix_system_message_subscriptions_event_type"), table_name="system_message_subscriptions")
    op.drop_index(op.f("ix_system_message_subscriptions_tenant_id"), table_name="system_message_subscriptions")
    op.drop_table("system_message_subscriptions")
    with op.batch_alter_table("webhook_endpoints") as batch_op:
        batch_op.drop_column("auth_username")
        batch_op.drop_column("recipient")
        batch_op.drop_column("credential_encrypted")
        batch_op.drop_column("channel_type")
