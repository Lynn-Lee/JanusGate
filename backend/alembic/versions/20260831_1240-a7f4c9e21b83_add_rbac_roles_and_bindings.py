"""add rbac roles and role bindings

Revision ID: a7f4c9e21b83
Revises: c8e4a91b7d02
Create Date: 2026-08-31 12:40:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f4c9e21b83"
down_revision: str | None = "c8e4a91b7d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("permissions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_roles_tenant_name"),
    )
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"], unique=False)

    op.create_table(
        "role_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("organization_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "role_id",
            "organization_id",
            name="uq_role_bindings_subject_role_org",
        ),
    )
    op.create_index(
        op.f("ix_role_bindings_tenant_id"), "role_bindings", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_bindings_user_id"), "role_bindings", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_bindings_role_id"), "role_bindings", ["role_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_role_bindings_role_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_user_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_tenant_id"), table_name="role_bindings")
    op.drop_table("role_bindings")
    op.drop_index(op.f("ix_roles_tenant_id"), table_name="roles")
    op.drop_table("roles")
