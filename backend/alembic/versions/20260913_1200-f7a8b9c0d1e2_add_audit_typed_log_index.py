"""add audit_events tenant+category index for #t78 typed logs

Revision ID: g8c9d0e1f2a4
Revises: g7b8c9d0e1f3
Create Date: 2026-09-13 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "g8c9d0e1f2a4"
down_revision: str | None = "g7b8c9d0e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_tenant_category",
        "audit_events",
        ["tenant_id", "category"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_category", table_name="audit_events")
