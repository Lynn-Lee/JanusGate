"""merge alembic heads for #t78 file transfer logs

Revision ID: f7b8c9d0e1f2
Revises: f6a7b8c9d0e1, f6b0d4c2e815
Create Date: 2026-09-08 13:10:00.000000

"""
from collections.abc import Sequence

revision: str = "f7b8c9d0e1f2"
down_revision: tuple[str, str] | None = ("f6a7b8c9d0e1", "f6b0d4c2e815")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """线性化 #t78 与既有 k8s asset 列分支，无额外 schema 变更。"""
    return


def downgrade() -> None:
    return
