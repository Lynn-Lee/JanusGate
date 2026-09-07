"""add account push status fields (#t73 account.push)

Also merges the parallel k8s-connect head ``f6b0d4c2e815`` into the main line.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0, f6b0d4c2e815
Create Date: 2026-09-07 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = ("e5f6a7b8c9d0", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "push_status",
            sa.String(length=20),
            nullable=False,
            server_default="unpushed",
        ),
    )
    op.add_column(
        "accounts",
        sa.Column("last_push_message_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("last_push_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "last_push_at")
    op.drop_column("accounts", "last_push_message_id")
    op.drop_column("accounts", "push_status")
