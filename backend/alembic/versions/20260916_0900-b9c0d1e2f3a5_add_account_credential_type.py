"""add account credential_type for account.push (#t73)

Revision ID: b9c0d1e2f3a5
Revises: g8c9d0e1f2a4
Create Date: 2026-09-07 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a5"
down_revision: str | None = "g8c9d0e1f2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("credential_type", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "credential_type")
