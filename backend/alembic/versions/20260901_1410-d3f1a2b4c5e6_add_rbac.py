"""add rbac roles bindings user groups object permissions

Revision ID: d3f1a2b4c5e6
Revises: c8e4a91b7d02
Create Date: 2026-09-01 14:10:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f1a2b4c5e6"
down_revision: str | None = "c8e4a91b7d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rbac_roles",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
        sa.Column("builtin_key", sa.String(length=64), nullable=True),
        sa.Column("permissions_json", sa.Text(), nullable=False),
        sa.Column("menu_permissions_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
    op.create_index(op.f("ix_rbac_roles_tenant_id"), "rbac_roles", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_rbac_roles_organization_id"), "rbac_roles", ["organization_id"], unique=False
    )
    op.create_index(
        "uq_rbac_roles_builtin_per_tenant",
        "rbac_roles",
        ["tenant_id", "builtin_key"],
        unique=True,
        sqlite_where=sa.text("builtin_key IS NOT NULL"),
        postgresql_where=sa.text("builtin_key IS NOT NULL"),
    )

    op.create_table(
        "rbac_role_bindings",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("role_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        op.f("ix_rbac_role_bindings_tenant_id"), "rbac_role_bindings", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_rbac_role_bindings_role_id"), "rbac_role_bindings", ["role_id"], unique=False
    )
    op.create_index(
        op.f("ix_rbac_role_bindings_subject_id"),
        "rbac_role_bindings",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_role_bindings_organization_id"),
        "rbac_role_bindings",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "rbac_user_groups",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("member_ids_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        op.f("ix_rbac_user_groups_tenant_id"), "rbac_user_groups", ["tenant_id"], unique=False
    )

    op.create_table(
        "rbac_object_permissions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        op.f("ix_rbac_object_permissions_tenant_id"),
        "rbac_object_permissions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_object_permissions_subject_id"),
        "rbac_object_permissions",
        ["subject_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_object_permissions_resource_type"),
        "rbac_object_permissions",
        ["resource_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_object_permissions_resource_id"),
        "rbac_object_permissions",
        ["resource_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_object_permissions_organization_id"),
        "rbac_object_permissions",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_rbac_object_permissions_organization_id"), table_name="rbac_object_permissions"
    )
    op.drop_index(
        op.f("ix_rbac_object_permissions_resource_id"), table_name="rbac_object_permissions"
    )
    op.drop_index(
        op.f("ix_rbac_object_permissions_resource_type"), table_name="rbac_object_permissions"
    )
    op.drop_index(
        op.f("ix_rbac_object_permissions_subject_id"), table_name="rbac_object_permissions"
    )
    op.drop_index(
        op.f("ix_rbac_object_permissions_tenant_id"), table_name="rbac_object_permissions"
    )
    op.drop_table("rbac_object_permissions")

    op.drop_index(op.f("ix_rbac_user_groups_tenant_id"), table_name="rbac_user_groups")
    op.drop_table("rbac_user_groups")

    op.drop_index(
        op.f("ix_rbac_role_bindings_organization_id"), table_name="rbac_role_bindings"
    )
    op.drop_index(op.f("ix_rbac_role_bindings_subject_id"), table_name="rbac_role_bindings")
    op.drop_index(op.f("ix_rbac_role_bindings_role_id"), table_name="rbac_role_bindings")
    op.drop_index(op.f("ix_rbac_role_bindings_tenant_id"), table_name="rbac_role_bindings")
    op.drop_table("rbac_role_bindings")

    op.drop_index("uq_rbac_roles_builtin_per_tenant", table_name="rbac_roles")
    op.drop_index(op.f("ix_rbac_roles_organization_id"), table_name="rbac_roles")
    op.drop_index(op.f("ix_rbac_roles_tenant_id"), table_name="rbac_roles")
    op.drop_table("rbac_roles")
