"""ACL 访问控制持久化模型（#t65 M1：命令过滤 ACL + 命令组）。

本模块落地 #t65 ACL 体系的首个派生类型——命令过滤 ACL，以及其引用的命令组。所有 ACL
共享的 BaseACL 语义（优先级 1-100、动作、复核人）在此以命令过滤 ACL 为首个具体实现确立，
后续登录 ACL / 资产登录 ACL / 连接方式 ACL / 数据脱敏规则复用同一范式。

选择器（subject / asset / account / command_group）沿用仓库既有的 JSON 列范式
（对齐 ``ApprovalPolicyModel`` 的 ``*_json`` 选择器），``"*"`` 表示通配。判定统一由
:class:`~app.policy.decision.PolicyDecisionService` 消费，不在别处旁路（#t65 约束）。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CommandGroupMatchType(StrEnum):
    """命令组的匹配方式：字面命令（词边界）或正则。"""

    COMMAND = "command"
    REGEX = "regex"


class CommandFilterAction(StrEnum):
    """命令过滤 ACL 命中后的动作。

    与 roadmap #t65 动作清单对齐（``face_verify`` / ``face_online`` 不做）；``change_secret``
    属账号类 ACL，不在命令过滤范围，故此处不含。
    """

    REJECT = "reject"
    ACCEPT = "accept"
    REVIEW = "review"
    WARNING = "warning"
    NOTICE = "notice"
    NOTIFY_AND_WARN = "notify_and_warn"


class CommandGroupModel(Base):
    """命令组：一组字面命令或正则，供命令过滤 ACL 引用。

    :ivar match_type: ``command`` 按词边界匹配字面命令名，``regex`` 按正则搜索。
    :ivar patterns_json: JSON 字符串数组，存字面命令或正则模式。
    """

    __tablename__ = "command_groups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    match_type: Mapped[CommandGroupMatchType] = mapped_column(
        String(16), nullable=False, default=CommandGroupMatchType.COMMAND
    )
    patterns_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CommandFilterAclModel(Base):
    """命令过滤 ACL：BaseACL（优先级 + 动作 + 复核人）+ 选择器 + 命令组引用。

    :ivar priority: 1-100，**小者优先**（BaseACL 语义）；判定时按其升序取首个命中者。
    :ivar action: 命中后的动作。
    :ivar reviewer_subject_ids_json: ``review`` 动作的复核人主体 ID JSON 数组。
    :ivar subject_ids_json / asset_ids_json / account_ids_json: 作用对象选择器 JSON 数组，
        ``"*"`` 通配。
    :ivar command_group_ids_json: 本 ACL 关联的命令组 ID JSON 数组。
    """

    __tablename__ = "command_filter_acls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    action: Mapped[CommandFilterAction] = mapped_column(
        String(20), nullable=False, default=CommandFilterAction.REJECT
    )
    reviewer_subject_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    subject_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default='["*"]')
    asset_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default='["*"]')
    account_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default='["*"]')
    command_group_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
