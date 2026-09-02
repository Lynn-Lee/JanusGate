"""add asset host key trust table

Revision ID: e5a9c3b1d704
Revises: c4d8e2f1a903
Create Date: 2026-09-02 07:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a9c3b1d704"
down_revision: str | None = "c4d8e2f1a903"
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
        "asset_host_keys",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("host", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("approved_public_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_fingerprint", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("pending_public_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("pending_fingerprint", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("pending_state", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("pending_status", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("workflow_request_id", sa.String(length=64), nullable=False, server_default=""),
        created_at,
        updated_at,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "asset_id", name="uq_asset_host_keys_tenant_asset"),
    )
    op.create_index(op.f("ix_asset_host_keys_tenant_id"), "asset_host_keys", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_asset_host_keys_asset_id"), "asset_host_keys", ["asset_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_asset_host_keys_asset_id"), table_name="asset_host_keys")
    op.drop_index(op.f("ix_asset_host_keys_tenant_id"), table_name="asset_host_keys")
    op.drop_table("asset_host_keys")
