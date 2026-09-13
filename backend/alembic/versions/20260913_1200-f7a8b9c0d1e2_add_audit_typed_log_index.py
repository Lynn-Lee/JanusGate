"""add audit_events tenant+category index for #t78 typed logs

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-09-13 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e5f6a7b8c9d0"
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
