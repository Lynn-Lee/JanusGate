"""add session share, endpoint routing and storage backends for #t78

Revision ID: b8c9d0e1f2a3
Revises: e5f6a7b8c9d0
Create Date: 2026-09-10 05:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_shares",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("guest_user_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_session_shares_tenant_id", "session_shares", ["tenant_id"])
    op.create_index("ix_session_shares_session_id", "session_shares", ["session_id"])
    op.create_index("ix_session_shares_guest_user_id", "session_shares", ["guest_user_id"])
    op.create_table(
        "connection_endpoints",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("host", sa.String(length=256), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_connection_endpoints_tenant_id", "connection_endpoints", ["tenant_id"])
    op.create_table(
        "endpoint_rules",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("match_protocol", sa.String(length=32), nullable=False),
        sa.Column("match_host_suffix", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("endpoint_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_endpoint_rules_tenant_id", "endpoint_rules", ["tenant_id"])
    op.create_index("ix_endpoint_rules_endpoint_id", "endpoint_rules", ["endpoint_id"])
    op.create_table(
        "session_storage_backends",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_session_storage_backends_tenant_id", "session_storage_backends", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_session_storage_backends_tenant_id", table_name="session_storage_backends")
    op.drop_table("session_storage_backends")
    op.drop_index("ix_endpoint_rules_endpoint_id", table_name="endpoint_rules")
    op.drop_index("ix_endpoint_rules_tenant_id", table_name="endpoint_rules")
    op.drop_table("endpoint_rules")
    op.drop_index("ix_connection_endpoints_tenant_id", table_name="connection_endpoints")
    op.drop_table("connection_endpoints")
    op.drop_index("ix_session_shares_guest_user_id", table_name="session_shares")
    op.drop_index("ix_session_shares_session_id", table_name="session_shares")
    op.drop_index("ix_session_shares_tenant_id", table_name="session_shares")
    op.drop_table("session_shares")
