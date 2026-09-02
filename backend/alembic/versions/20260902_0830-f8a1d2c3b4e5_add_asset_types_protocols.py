"""add asset types and protocol models

Revision ID: f8a1d2c3b4e5
Revises: e5a9c3b1d704
Create Date: 2026-09-02 08:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a1d2c3b4e5"
down_revision: str | None = "e5a9c3b1d704"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "protocols",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("default_port", sa.Integer(), nullable=False),
        sa.Column("asset_types_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("credential_types_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("driver_module", sa.String(length=128), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "platform_protocols",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("protocol_id", sa.String(length=32), nullable=False),
        sa.Column("port", sa.Integer(), nullable=True),
        sa.Column("settings_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"]),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_platform_protocols_platform_id"),
        "platform_protocols",
        ["platform_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_platform_protocols_protocol_id"),
        "platform_protocols",
        ["protocol_id"],
        unique=False,
    )

    op.add_column(
        "platforms",
        sa.Column("asset_type", sa.String(length=32), nullable=False, server_default="host"),
    )
    op.add_column(
        "assets",
        sa.Column("asset_type", sa.String(length=32), nullable=False, server_default="host"),
    )
    op.create_index(op.f("ix_assets_asset_type"), "assets", ["asset_type"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_asset_type"), table_name="assets")
    op.drop_column("assets", "asset_type")
    op.drop_column("platforms", "asset_type")
    op.drop_index(op.f("ix_platform_protocols_protocol_id"), table_name="platform_protocols")
    op.drop_index(op.f("ix_platform_protocols_platform_id"), table_name="platform_protocols")
    op.drop_table("platform_protocols")
    op.drop_table("protocols")
