"""Phase 6 #t78 文件传输日志（对标 JumpServer FTPLog）。

文件内容本身不落库，只保存路径、方向、字节数与 SHA-256。每条日志必须先写入
#t61 审计 hash chain，再以 ``audit_event_id`` 回指，避免出现无链的分类日志。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FileTransferLog(Base):
    """一次 SFTP 上传/下载的分类审计日志。

    失败传输同样落库（``status=failed``），保证失败在审计中可见。
    ``remote_path`` 在入库前脱敏赋值片段，不保存文件正文。
    """

    __tablename__ = "file_transfer_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recording_id: Mapped[int] = mapped_column(
        ForeignKey("session_recordings.id"), nullable=False, index=True
    )
    session_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(120), nullable=False)
    account_id: Mapped[str] = mapped_column(String(120), nullable=False)
    remote_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    audit_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_file_transfer_logs_tenant_occurred_id",
            "tenant_id",
            "occurred_at",
            "id",
        ),
    )
