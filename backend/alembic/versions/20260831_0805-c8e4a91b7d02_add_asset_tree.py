"""add asset tree and asset permissions

Revision ID: c8e4a91b7d02
Revises: 4eb764da4aab
Create Date: 2026-08-31 08:05:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e4a91b7d02"
down_revision: str | None = "4eb764da4aab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_nodes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ancestor_ids_json", sa.Text(), nullable=False),
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
    op.create_index(
        op.f("ix_asset_nodes_tenant_id"), "asset_nodes", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_asset_nodes_parent_id"), "asset_nodes", ["parent_id"], unique=False
    )
    op.create_index(
        "uq_asset_nodes_one_root_per_tenant",
        "asset_nodes",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
        sqlite_where=sa.text("parent_id IS NULL"),
    )

    op.create_table(
        "asset_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("resource_type", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_ticket", sa.String(length=128), nullable=True),
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
    op.create_index(
        op.f("ix_asset_permissions_tenant_id"),
        "asset_permissions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_permissions_subject_id"),
        "asset_permissions",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_asset_permissions_resource_id"),
        "asset_permissions",
        ["resource_id"],
        unique=False,
    )

    op.add_column(
        "assets",
        sa.Column("node_id", sa.String(length=64), nullable=True),
    )
    op.create_index(op.f("ix_assets_node_id"), "assets", ["node_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_node_id"), table_name="assets")
    op.drop_column("assets", "node_id")
    op.drop_index(op.f("ix_asset_permissions_resource_id"), table_name="asset_permissions")
    op.drop_index(op.f("ix_asset_permissions_subject_id"), table_name="asset_permissions")
    op.drop_index(op.f("ix_asset_permissions_tenant_id"), table_name="asset_permissions")
    op.drop_table("asset_permissions")
    op.drop_index("uq_asset_nodes_one_root_per_tenant", table_name="asset_nodes")
    op.drop_index(op.f("ix_asset_nodes_parent_id"), table_name="asset_nodes")
    op.drop_index(op.f("ix_asset_nodes_tenant_id"), table_name="asset_nodes")
    op.drop_table("asset_nodes")
