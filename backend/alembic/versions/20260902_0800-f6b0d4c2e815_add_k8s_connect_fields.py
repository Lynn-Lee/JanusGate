"""add k8s namespace and API server CA on asset

Revision ID: f6b0d4c2e815
Revises: e5a9c3b1d704
Create Date: 2026-09-02 08:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6b0d4c2e815"
down_revision: str | None = "e5a9c3b1d704"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("namespace", sa.String(length=253), nullable=False, server_default=""),
    )
    op.add_column(
        "assets",
        sa.Column("server_ca", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("assets", "server_ca")
    op.drop_column("assets", "namespace")
