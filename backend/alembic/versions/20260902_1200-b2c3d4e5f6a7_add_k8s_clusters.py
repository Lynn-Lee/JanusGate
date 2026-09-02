"""add k8s clusters and account k8s scope

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-02 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "k8s_clusters",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("api_server", sa.String(length=512), nullable=False),
        sa.Column("server_ca_pem", sa.Text(), nullable=False, server_default=""),
        sa.Column("namespaces_json", sa.Text(), nullable=False, server_default="[]"),
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
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id"),
    )
    op.create_index(op.f("ix_k8s_clusters_asset_id"), "k8s_clusters", ["asset_id"], unique=True)
    op.create_index(op.f("ix_k8s_clusters_tenant_id"), "k8s_clusters", ["tenant_id"], unique=False)

    op.add_column(
        "accounts",
        sa.Column("k8s_namespaces_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "k8s_service_account",
            sa.String(length=253),
            nullable=False,
            server_default="default",
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("k8s_default_pod", sa.String(length=253), nullable=False, server_default=""),
    )
    op.add_column(
        "accounts",
        sa.Column("k8s_default_container", sa.String(length=253), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "k8s_use_short_lived_token",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "k8s_token_ttl_seconds",
            sa.Integer(),
            nullable=False,
            server_default="3600",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "k8s_token_ttl_seconds")
    op.drop_column("accounts", "k8s_use_short_lived_token")
    op.drop_column("accounts", "k8s_default_container")
    op.drop_column("accounts", "k8s_default_pod")
    op.drop_column("accounts", "k8s_service_account")
    op.drop_column("accounts", "k8s_namespaces_json")
    op.drop_index(op.f("ix_k8s_clusters_tenant_id"), table_name="k8s_clusters")
    op.drop_index(op.f("ix_k8s_clusters_asset_id"), table_name="k8s_clusters")
    op.drop_table("k8s_clusters")
