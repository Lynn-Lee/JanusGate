"""add account k8s TokenRequest fields (#t68)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-07 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "use_token_request",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "token_ttl_seconds",
            sa.Integer(),
            nullable=False,
            server_default="900",
        ),
    )


def downgrade() -> None:
    op.drop_column("accounts", "token_ttl_seconds")
    op.drop_column("accounts", "use_token_request")
