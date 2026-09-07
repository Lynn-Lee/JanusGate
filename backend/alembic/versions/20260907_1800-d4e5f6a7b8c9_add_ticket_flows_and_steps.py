"""add ticket flows, approval rules, ticket steps (#t74)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-07 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ticket_flows",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("flow_type", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
    op.create_index(op.f("ix_ticket_flows_tenant_id"), "ticket_flows", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ticket_flows_enabled"), "ticket_flows", ["enabled"], unique=False)

    op.create_table(
        "approval_rules",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("ticket_flow_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("approver_user_ids_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_approval_rules_tenant_id"), "approval_rules", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_approval_rules_ticket_flow_id"), "approval_rules", ["ticket_flow_id"], unique=False
    )

    op.create_table(
        "ticket_steps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_request_id", sa.String(length=64), nullable=False),
        sa.Column("ticket_flow_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approver_user_ids_json", sa.Text(), nullable=False),
        sa.Column("decided_by_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("decided_by_username", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ticket_steps_tenant_id"), "ticket_steps", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_ticket_steps_workflow_request_id"),
        "ticket_steps",
        ["workflow_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_ticket_steps_ticket_flow_id"), "ticket_steps", ["ticket_flow_id"], unique=False
    )

    op.add_column(
        "workflow_requests",
        sa.Column("ticket_flow_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "workflow_requests",
        sa.Column("current_level", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "workflow_requests",
        sa.Column("total_levels", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        op.f("ix_workflow_requests_ticket_flow_id"),
        "workflow_requests",
        ["ticket_flow_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_requests_ticket_flow_id"), table_name="workflow_requests")
    op.drop_column("workflow_requests", "total_levels")
    op.drop_column("workflow_requests", "current_level")
    op.drop_column("workflow_requests", "ticket_flow_id")
    op.drop_index(op.f("ix_ticket_steps_ticket_flow_id"), table_name="ticket_steps")
    op.drop_index(op.f("ix_ticket_steps_workflow_request_id"), table_name="ticket_steps")
    op.drop_index(op.f("ix_ticket_steps_tenant_id"), table_name="ticket_steps")
    op.drop_table("ticket_steps")
    op.drop_index(op.f("ix_approval_rules_ticket_flow_id"), table_name="approval_rules")
    op.drop_index(op.f("ix_approval_rules_tenant_id"), table_name="approval_rules")
    op.drop_table("approval_rules")
    op.drop_index(op.f("ix_ticket_flows_enabled"), table_name="ticket_flows")
    op.drop_index(op.f("ix_ticket_flows_tenant_id"), table_name="ticket_flows")
    op.drop_table("ticket_flows")
