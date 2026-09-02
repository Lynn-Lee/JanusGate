"""add overlay login login-asset and connect-method acls

Revision ID: b7e2c91d4a08
Revises: d3f1a82b9c10
Create Date: 2026-09-02 05:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2c91d4a08"
down_revision: str | None = "d3f1a82b9c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    created_at, updated_at = _timestamps()
    op.create_table(
        "login_acls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="reject"),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        created_at,
        updated_at,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_login_acls_tenant_id"), "login_acls", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_login_acls_subject_id"), "login_acls", ["subject_id"], unique=False)

    created_at, updated_at = _timestamps()
    op.create_table(
        "login_asset_acls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="reject"),
        sa.Column("resource_type", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("ip_cidr", sa.String(length=64), nullable=True),
        sa.Column("time_start", sa.String(length=8), nullable=True),
        sa.Column("time_end", sa.String(length=8), nullable=True),
        created_at,
        updated_at,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_login_asset_acls_tenant_id"), "login_asset_acls", ["tenant_id"], unique=False
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "connect_method_acls",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("action", sa.String(length=16), nullable=False, server_default="reject"),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=16), nullable=True),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        created_at,
        updated_at,
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_connect_method_acls_tenant_id"),
        "connect_method_acls",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_connect_method_acls_tenant_id"), table_name="connect_method_acls")
    op.drop_table("connect_method_acls")
    op.drop_index(op.f("ix_login_asset_acls_tenant_id"), table_name="login_asset_acls")
    op.drop_table("login_asset_acls")
    op.drop_index(op.f("ix_login_acls_subject_id"), table_name="login_acls")
    op.drop_index(op.f("ix_login_acls_tenant_id"), table_name="login_acls")
    op.drop_table("login_acls")
