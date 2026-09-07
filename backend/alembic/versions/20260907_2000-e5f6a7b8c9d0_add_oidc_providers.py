"""add oidc_providers for #t76 OIDC login

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-07 20:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oidc_providers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("issuer_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("client_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "scopes",
            sa.String(length=255),
            nullable=False,
            server_default="openid profile email",
        ),
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
        sa.UniqueConstraint("tenant_id", name="uq_oidc_providers_tenant_id"),
    )
    op.create_index(op.f("ix_oidc_providers_tenant_id"), "oidc_providers", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oidc_providers_tenant_id"), table_name="oidc_providers")
    op.drop_table("oidc_providers")
