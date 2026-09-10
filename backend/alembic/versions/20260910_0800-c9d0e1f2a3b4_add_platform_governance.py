"""add platform governance tables for #t79

Revision ID: c9d0e1f2a3b4
Revises: e5f6a7b8c9d0
Create Date: 2026-09-10 08:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#2563eb"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_resource_labels_tenant_name"),
    )
    op.create_index(op.f("ix_resource_labels_tenant_id"), "resource_labels", ["tenant_id"], unique=False)

    op.create_table(
        "resource_label_bindings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("label_id", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False, server_default="asset"),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["label_id"], ["resource_labels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "label_id",
            "resource_type",
            "resource_id",
            name="uq_resource_label_bindings_target",
        ),
    )
    op.create_index(
        op.f("ix_resource_label_bindings_tenant_id"),
        "resource_label_bindings",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_resource_label_bindings_label_id"),
        "resource_label_bindings",
        ["label_id"],
        unique=False,
    )

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_tenant_key"),
    )
    op.create_index(op.f("ix_tenant_settings_tenant_id"), "tenant_settings", ["tenant_id"], unique=False)

    op.create_table(
        "tenant_setting_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("old_value_json", sa.Text(), nullable=False),
        sa.Column("new_value_json", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tenant_setting_revisions_tenant_id"),
        "tenant_setting_revisions",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "key", name="uq_user_preferences_actor_key"),
    )
    op.create_index(op.f("ix_user_preferences_tenant_id"), "user_preferences", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_user_preferences_user_id"), "user_preferences", ["user_id"], unique=False)

    op.create_table(
        "leak_passwords",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "sha256", name="uq_leak_passwords_tenant_hash"),
    )
    op.create_index(op.f("ix_leak_passwords_tenant_id"), "leak_passwords", ["tenant_id"], unique=False)

    op.create_table(
        "report_definitions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_report_definitions_tenant_name"),
    )
    op.create_index(
        op.f("ix_report_definitions_tenant_id"), "report_definitions", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_definitions_tenant_id"), table_name="report_definitions")
    op.drop_table("report_definitions")
    op.drop_index(op.f("ix_leak_passwords_tenant_id"), table_name="leak_passwords")
    op.drop_table("leak_passwords")
    op.drop_index(op.f("ix_user_preferences_user_id"), table_name="user_preferences")
    op.drop_index(op.f("ix_user_preferences_tenant_id"), table_name="user_preferences")
    op.drop_table("user_preferences")
    op.drop_index(op.f("ix_tenant_setting_revisions_tenant_id"), table_name="tenant_setting_revisions")
    op.drop_table("tenant_setting_revisions")
    op.drop_index(op.f("ix_tenant_settings_tenant_id"), table_name="tenant_settings")
    op.drop_table("tenant_settings")
    op.drop_index(op.f("ix_resource_label_bindings_label_id"), table_name="resource_label_bindings")
    op.drop_index(op.f("ix_resource_label_bindings_tenant_id"), table_name="resource_label_bindings")
    op.drop_table("resource_label_bindings")
    op.drop_index(op.f("ix_resource_labels_tenant_id"), table_name="resource_labels")
    op.drop_table("resource_labels")
