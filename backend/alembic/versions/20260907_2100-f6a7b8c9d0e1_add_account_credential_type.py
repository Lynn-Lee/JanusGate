"""add account credential_type for account.push (#t73)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-07 21:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("credential_type", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("accounts", "credential_type")
