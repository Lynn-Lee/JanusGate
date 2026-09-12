"""add zones and asset zone_id

Revision ID: a1b2c3d4e5f6
Revises: f8a1d2c3b4e5
Create Date: 2026-09-07 09:00:00.000000

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
        "zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
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
    )
    op.create_index(op.f("ix_zones_tenant_id"), "zones", ["tenant_id"], unique=False)

    op.create_table(
        "zone_gateways",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("zone_id", "asset_id", name="uq_zone_gateways_zone_asset"),
    )
    op.create_index(op.f("ix_zone_gateways_tenant_id"), "zone_gateways", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_zone_gateways_zone_id"), "zone_gateways", ["zone_id"], unique=False)
    op.create_index(op.f("ix_zone_gateways_asset_id"), "zone_gateways", ["asset_id"], unique=False)

    op.add_column("assets", sa.Column("zone_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_assets_zone_id"), "assets", ["zone_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_zone_id"), table_name="assets")
    op.drop_column("assets", "zone_id")
    op.drop_index(op.f("ix_zone_gateways_asset_id"), table_name="zone_gateways")
    op.drop_index(op.f("ix_zone_gateways_zone_id"), table_name="zone_gateways")
    op.drop_index(op.f("ix_zone_gateways_tenant_id"), table_name="zone_gateways")
    op.drop_table("zone_gateways")
    op.drop_index(op.f("ix_zones_tenant_id"), table_name="zones")
    op.drop_table("zones")
