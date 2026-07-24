"""#t65 M1：ACL / 脱敏规则持久化加载端到端测试。

用异步 sqlite 内存库落库多租户的命令过滤 ACL、命令组、脱敏规则，验证加载器只取本租户
且 active 的记录，并把装配出的 PolicyDecisionService 实际用于 evaluate_command / mask。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.acl import (
    CommandFilterAclModel,
    CommandFilterAction,
    CommandGroupMatchType,
    CommandGroupModel,
    DataMaskingMatchType,
    DataMaskingMethod,
    DataMaskingRuleModel,
)
from app.policy.repository import AclRepository, build_tenant_policy_service
from app.policy.schemas import (
    CommandDecisionRequest,
    CommandFilterEffect,
    MaskingRequest,
    ResourceRef,
    SubjectRef,
)
from app.tenancy.scope import ActorScope


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _scope(tenant_id: str = "tenant-a") -> ActorScope:
    return ActorScope(user_id="user-1", tenant_id=tenant_id)


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            CommandGroupModel(
                id="grp-rm",
                tenant_id="tenant-a",
                name="danger",
                match_type=CommandGroupMatchType.COMMAND,
                patterns_json=json.dumps(["rm"]),
                is_active=True,
            ),
            CommandGroupModel(
                id="grp-inactive",
                tenant_id="tenant-a",
                name="inactive",
                match_type=CommandGroupMatchType.COMMAND,
                patterns_json=json.dumps(["ls"]),
                is_active=False,
            ),
            CommandGroupModel(
                id="grp-other-tenant",
                tenant_id="tenant-b",
                name="other",
                match_type=CommandGroupMatchType.COMMAND,
                patterns_json=json.dumps(["rm"]),
                is_active=True,
            ),
            CommandFilterAclModel(
                id="acl-a",
                tenant_id="tenant-a",
                name="deny-rm",
                priority=10,
                action=CommandFilterAction.REJECT,
                reviewer_subject_ids_json="[]",
                subject_ids_json=json.dumps(["*"]),
                asset_ids_json=json.dumps(["*"]),
                account_ids_json=json.dumps(["*"]),
                command_group_ids_json=json.dumps(["grp-rm"]),
                is_active=True,
            ),
            CommandFilterAclModel(
                id="acl-b-other-tenant",
                tenant_id="tenant-b",
                name="deny-rm-b",
                priority=10,
                action=CommandFilterAction.REJECT,
                reviewer_subject_ids_json="[]",
                subject_ids_json=json.dumps(["*"]),
                asset_ids_json=json.dumps(["*"]),
                account_ids_json=json.dumps(["*"]),
                command_group_ids_json=json.dumps(["grp-other-tenant"]),
                is_active=True,
            ),
            DataMaskingRuleModel(
                id="mask-a",
                tenant_id="tenant-a",
                name="ssn",
                priority=10,
                match_type=DataMaskingMatchType.REGEX,
                patterns_json=json.dumps([r"\d{3}-\d{2}-\d{4}"]),
                mask_method=DataMaskingMethod.FULL,
                keep_prefix=0,
                keep_suffix=0,
                placeholder="[SSN]",
                subject_ids_json=json.dumps(["*"]),
                asset_ids_json=json.dumps(["*"]),
                account_ids_json=json.dumps(["*"]),
                is_active=True,
            ),
            DataMaskingRuleModel(
                id="mask-inactive",
                tenant_id="tenant-a",
                name="inactive",
                priority=20,
                match_type=DataMaskingMatchType.REGEX,
                patterns_json=json.dumps([r"\d+"]),
                mask_method=DataMaskingMethod.FULL,
                keep_prefix=0,
                keep_suffix=0,
                placeholder="#",
                subject_ids_json=json.dumps(["*"]),
                asset_ids_json=json.dumps(["*"]),
                account_ids_json=json.dumps(["*"]),
                is_active=False,
            ),
        ]
    )
    await session.commit()


async def test_repository_loads_only_active_tenant_scoped_records(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        repo = AclRepository(session)

        acls = await repo.list_command_filter_acls(_scope("tenant-a"))
        groups = await repo.list_command_groups(_scope("tenant-a"))
        masks = await repo.list_data_masking_rules(_scope("tenant-a"))

    assert {a.id for a in acls} == {"acl-a"}
    assert {g.id for g in groups} == {"grp-rm"}
    assert {m.id for m in masks} == {"mask-a"}


async def test_other_tenant_sees_none_of_tenant_a_records(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        repo = AclRepository(session)

        acls = await repo.list_command_filter_acls(_scope("tenant-c"))
        masks = await repo.list_data_masking_rules(_scope("tenant-c"))

    assert acls == []
    assert masks == []


async def test_built_service_enforces_loaded_command_filter(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        service = await build_tenant_policy_service(session, _scope("tenant-a"))

    result = service.evaluate_command(
        CommandDecisionRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="asset-1", type="ssh_asset", tenant_id="tenant-a"),
            account_id="root",
            command="rm -rf /data",
        )
    )

    assert result.effect is CommandFilterEffect.DENY
    assert result.matched_acl_id == "acl-a"


async def test_built_service_applies_loaded_masking(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        service = await build_tenant_policy_service(session, _scope("tenant-a"))

    result = service.mask(
        MaskingRequest(
            subject=SubjectRef(id="user-1", tenant_id="tenant-a"),
            resource=ResourceRef(id="asset-1", type="db_asset", tenant_id="tenant-a"),
            account_id="root",
            text="ssn 123-45-6789",
        )
    )

    assert result.masked_text == "ssn [SSN]"
    assert result.applied_rule_ids == ["mask-a"]


async def test_built_service_for_other_tenant_has_no_rules(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await _seed(session)
        service = await build_tenant_policy_service(session, _scope("tenant-c"))

    # 本租户无 ACL：命令默认放行、文本原样。
    command_result = service.evaluate_command(
        CommandDecisionRequest(
            subject=SubjectRef(id="user-9", tenant_id="tenant-c"),
            resource=ResourceRef(id="asset-9", type="ssh_asset", tenant_id="tenant-c"),
            account_id="root",
            command="rm -rf /data",
        )
    )
    mask_result = service.mask(
        MaskingRequest(
            subject=SubjectRef(id="user-9", tenant_id="tenant-c"),
            resource=ResourceRef(id="asset-9", type="db_asset", tenant_id="tenant-c"),
            account_id="root",
            text="ssn 123-45-6789",
        )
    )

    assert command_result.effect is CommandFilterEffect.ALLOW
    assert mask_result.masked_text == "ssn 123-45-6789"
