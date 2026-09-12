"""#t78 session shares, endpoints, storage backends; merge alembic heads

Revision ID: c8d9e0f1a2b3
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-12 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: tuple[str, str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_shares",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(op.f("ix_session_shares_tenant_id"), "session_shares", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_session_shares_session_id"), "session_shares", ["session_id"], unique=False)

    op.create_table(
        "session_join_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("share_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("joiner_id", sa.String(length=64), nullable=False),
        sa.Column("joiner_username", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="observe"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_session_join_records_tenant_id"), "session_join_records", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_session_join_records_share_id"), "session_join_records", ["share_id"], unique=False
    )
    op.create_index(
        op.f("ix_session_join_records_session_id"),
        "session_join_records",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "session_endpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_session_endpoints_tenant_name"),
    )
    op.create_index(
        op.f("ix_session_endpoints_tenant_id"), "session_endpoints", ["tenant_id"], unique=False
    )

    op.create_table(
        "session_endpoint_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("match_protocol", sa.String(length=32), nullable=False),
        sa.Column("match_asset_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_session_endpoint_rules_tenant_id"),
        "session_endpoint_rules",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_session_endpoint_rules_endpoint_id"),
        "session_endpoint_rules",
        ["endpoint_id"],
        unique=False,
    )

    op.create_table(
        "session_storage_backends",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "kind", "name", name="uq_session_storage_tenant_kind_name"),
    )
    op.create_index(
        op.f("ix_session_storage_backends_tenant_id"),
        "session_storage_backends",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_session_storage_backends_tenant_id"), table_name="session_storage_backends")
    op.drop_table("session_storage_backends")
    op.drop_index(op.f("ix_session_endpoint_rules_endpoint_id"), table_name="session_endpoint_rules")
    op.drop_index(op.f("ix_session_endpoint_rules_tenant_id"), table_name="session_endpoint_rules")
    op.drop_table("session_endpoint_rules")
    op.drop_index(op.f("ix_session_endpoints_tenant_id"), table_name="session_endpoints")
    op.drop_table("session_endpoints")
    op.drop_index(op.f("ix_session_join_records_session_id"), table_name="session_join_records")
    op.drop_index(op.f("ix_session_join_records_share_id"), table_name="session_join_records")
    op.drop_index(op.f("ix_session_join_records_tenant_id"), table_name="session_join_records")
    op.drop_table("session_join_records")
    op.drop_index(op.f("ix_session_shares_session_id"), table_name="session_shares")
    op.drop_index(op.f("ix_session_shares_tenant_id"), table_name="session_shares")
    op.drop_table("session_shares")
