"""add file_transfer_logs for #t78 FTPLog slice

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-08 13:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_transfer_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("recording_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=120), nullable=False),
        sa.Column("asset_id", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("remote_path", sa.String(length=1024), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=False),
        sa.Column("audit_event_id", sa.String(length=64), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["recording_id"], ["session_recordings.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_file_transfer_logs_tenant_id"),
        "file_transfer_logs",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_file_transfer_logs_recording_id"),
        "file_transfer_logs",
        ["recording_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_file_transfer_logs_session_id"),
        "file_transfer_logs",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_file_transfer_logs_audit_event_id"),
        "file_transfer_logs",
        ["audit_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_file_transfer_logs_tenant_occurred_id",
        "file_transfer_logs",
        ["tenant_id", "occurred_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_file_transfer_logs_tenant_occurred_id", table_name="file_transfer_logs")
    op.drop_index(op.f("ix_file_transfer_logs_audit_event_id"), table_name="file_transfer_logs")
    op.drop_index(op.f("ix_file_transfer_logs_session_id"), table_name="file_transfer_logs")
    op.drop_index(op.f("ix_file_transfer_logs_recording_id"), table_name="file_transfer_logs")
    op.drop_index(op.f("ix_file_transfer_logs_tenant_id"), table_name="file_transfer_logs")
    op.drop_table("file_transfer_logs")
