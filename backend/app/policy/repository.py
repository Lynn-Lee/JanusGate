"""ACL / 数据脱敏规则的持久化加载（#t65 M1：把判定接上数据库）。

本模块把 #t65 已落地的命令过滤 ACL、命令组、数据脱敏规则从数据库装载进
:class:`~app.policy.decision.PolicyDecisionService`，闭合「模型在库、判定在服务、但生产
无从装载」的缺口。

所有授权查询**强制走租户 scope helper** :func:`~app.tenancy.scope.scoped_select`（落实
#t64「授权查询强制走 scope helper」、关闭 P2#9 的 174 处无过滤查询）。ACL 模型只带
``tenant_id``、不带 org/team/project 列，故 ``scoped_select`` 对它们只施加租户过滤——
恰好是「装载某租户全量 ACL 供判定」所需，且不会被调用者的 org/team 子范围误缩小。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.acl import (
    CommandFilterAclModel,
    CommandGroupModel,
    ConnectMethodAclModel,
    DataMaskingRuleModel,
    LoginAclModel,
    LoginAssetAclModel,
)
from app.models.workflow import ApprovalPolicyModel
from app.policy.asset_tree_repository import AssetTreeRepository
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import PolicyRule
from app.tenancy.scope import ActorScope, scoped_select
from app.tenancy.tenant import ensure_tenant


class AclRepository:
    """按租户 scope 加载命令过滤 ACL、命令组与数据脱敏规则。

    命令过滤 / 脱敏仅返回 ``is_active`` 的记录；overlay ACL 无启用开关，全部参与判定。
    查询统一经 :func:`scoped_select`，杜绝跨租户泄露。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_command_filter_acls(
        self, actor_scope: ActorScope
    ) -> list[CommandFilterAclModel]:
        """加载租户内活跃的命令过滤 ACL。"""

        statement = scoped_select(CommandFilterAclModel, actor_scope).where(
            CommandFilterAclModel.is_active.is_(True)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_command_groups(self, actor_scope: ActorScope) -> list[CommandGroupModel]:
        """加载租户内活跃的命令组。"""

        statement = scoped_select(CommandGroupModel, actor_scope).where(
            CommandGroupModel.is_active.is_(True)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_data_masking_rules(
        self, actor_scope: ActorScope
    ) -> list[DataMaskingRuleModel]:
        """加载租户内活跃的数据脱敏规则。"""

        statement = scoped_select(DataMaskingRuleModel, actor_scope).where(
            DataMaskingRuleModel.is_active.is_(True)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_login_acls(self, actor_scope: ActorScope) -> list[LoginAclModel]:
        """加载租户内全部登录 ACL（无 is_active 开关）。"""

        result = await self._session.execute(scoped_select(LoginAclModel, actor_scope))
        return list(result.scalars().all())

    async def list_login_asset_acls(
        self, actor_scope: ActorScope
    ) -> list[LoginAssetAclModel]:
        """加载租户内全部资产登录 ACL。"""

        result = await self._session.execute(scoped_select(LoginAssetAclModel, actor_scope))
        return list(result.scalars().all())

    async def list_connect_method_acls(
        self, actor_scope: ActorScope
    ) -> list[ConnectMethodAclModel]:
        """加载租户内全部连接方式 ACL。"""

        result = await self._session.execute(
            scoped_select(ConnectMethodAclModel, actor_scope)
        )
        return list(result.scalars().all())


async def build_tenant_policy_service(
    session: AsyncSession,
    actor_scope: ActorScope,
    *,
    rules: list[PolicyRule] | None = None,
    approval_policies: list[ApprovalPolicyModel] | None = None,
) -> PolicyDecisionService:
    """装配一个已加载某租户 ACL / 脱敏规则的 :class:`PolicyDecisionService`。

    会话级规则（``rules`` / ``approval_policies``）由调用方按需传入（其加载归属工作流仓库），
    本工厂装载 #t65 命令过滤 ACL / 命令组 / 脱敏规则，以及 #t64 节点与 AssetPermission
    （connect 判定走 AssetPermission，overlay 语义不变），返回可直接
    ``evaluate`` / ``evaluate_command`` / ``mask`` 的服务实例。
    """

    tenant = await ensure_tenant(session, actor_scope.tenant_id)
    repository = AclRepository(session)
    tree = AssetTreeRepository(session)
    return PolicyDecisionService(
        rules=rules or [],
        approval_policies=approval_policies or [],
        command_filter_acls=await repository.list_command_filter_acls(actor_scope),
        command_groups=await repository.list_command_groups(actor_scope),
        data_masking_rules=await repository.list_data_masking_rules(actor_scope),
        asset_permissions=await tree.list_permissions(actor_scope),
        nodes=await tree.list_nodes(actor_scope),
        asset_node_ids=await tree.list_asset_node_ids(actor_scope),
        login_acls=await repository.list_login_acls(actor_scope),
        login_asset_acls=await repository.list_login_asset_acls(actor_scope),
        connect_method_acls=await repository.list_connect_method_acls(actor_scope),
        tenant_timezone=tenant.timezone,
    )
