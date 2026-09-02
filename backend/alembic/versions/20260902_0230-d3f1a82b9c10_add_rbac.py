"""add rbac roles bindings and object permissions

Revision ID: d3f1a82b9c10
Revises: c8e4a91b7d02
Create Date: 2026-09-02 02:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f1a82b9c10"
down_revision: str | None = "c8e4a91b7d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("builtin_key", sa.String(length=32), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("permissions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("menu_permissions_json", sa.Text(), nullable=False, server_default="[]"),
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
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_roles_organization_id"), "roles", ["organization_id"], unique=False
    )
    op.create_index(
        "uq_roles_tenant_scope_name",
        "roles",
        ["tenant_id", "scope_type", "organization_id", "name"],
        unique=True,
    )

    op.create_table(
        "role_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
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
        op.f("ix_role_bindings_tenant_id"), "role_bindings", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_bindings_role_id"), "role_bindings", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_bindings_subject_id"), "role_bindings", ["subject_id"], unique=False
    )
    op.create_index(
        op.f("ix_role_bindings_organization_id"),
        "role_bindings",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "uq_role_bindings_subject_role",
        "role_bindings",
        [
            "tenant_id",
            "role_id",
            "subject_type",
            "subject_id",
            "scope_type",
            "organization_id",
        ],
        unique=True,
    )

    op.create_table(
        "role_object_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_role_object_permissions_tenant_id"),
        "role_object_permissions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_role_object_permissions_role_id"),
        "role_object_permissions",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_role_object_permissions_resource_id"),
        "role_object_permissions",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        "uq_role_object_permissions_unique",
        "role_object_permissions",
        ["tenant_id", "role_id", "resource_type", "resource_id", "action"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_role_object_permissions_unique", table_name="role_object_permissions")
    op.drop_index(
        op.f("ix_role_object_permissions_resource_id"), table_name="role_object_permissions"
    )
    op.drop_index(
        op.f("ix_role_object_permissions_role_id"), table_name="role_object_permissions"
    )
    op.drop_index(
        op.f("ix_role_object_permissions_tenant_id"), table_name="role_object_permissions"
    )
    op.drop_table("role_object_permissions")

    op.drop_index("uq_role_bindings_subject_role", table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_organization_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_subject_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_role_id"), table_name="role_bindings")
    op.drop_index(op.f("ix_role_bindings_tenant_id"), table_name="role_bindings")
    op.drop_table("role_bindings")

    op.drop_index("uq_roles_tenant_scope_name", table_name="roles")
    op.drop_index(op.f("ix_roles_organization_id"), table_name="roles")
    op.drop_index(op.f("ix_roles_tenant_id"), table_name="roles")
    op.drop_table("roles")
