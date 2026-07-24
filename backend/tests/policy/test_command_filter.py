"""#t65 M1：命令过滤 ACL 判定（PolicyDecisionService.evaluate_command）测试。

覆盖：无 ACL 默认放行、reject 拦截、优先级序、review 返回复核人、正则组、字面命令词边界、
选择器作用域、租户隔离、非法正则安全 no-match、告警/通知 obligation。ACL / 命令组以 ORM
模型在内存直接构造（不落库），与判定服务解耦。
"""

from __future__ import annotations

import json

from app.models.acl import (
    CommandFilterAclModel,
    CommandFilterAction,
    CommandGroupMatchType,
    CommandGroupModel,
)
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import (
    CommandDecisionRequest,
    CommandFilterEffect,
    ResourceRef,
    SubjectRef,
)


def _group(
    group_id: str,
    patterns: list[str],
    *,
    match_type: CommandGroupMatchType = CommandGroupMatchType.COMMAND,
    tenant_id: str = "tenant-a",
    is_active: bool = True,
) -> CommandGroupModel:
    return CommandGroupModel(
        id=group_id,
        tenant_id=tenant_id,
        name=group_id,
        match_type=match_type,
        patterns_json=json.dumps(patterns),
        is_active=is_active,
    )


def _acl(
    acl_id: str,
    command_group_ids: list[str],
    *,
    action: CommandFilterAction = CommandFilterAction.REJECT,
    priority: int = 50,
    tenant_id: str = "tenant-a",
    subject_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
    account_ids: list[str] | None = None,
    reviewers: list[str] | None = None,
    is_active: bool = True,
) -> CommandFilterAclModel:
    return CommandFilterAclModel(
        id=acl_id,
        tenant_id=tenant_id,
        name=acl_id,
        priority=priority,
        action=action,
        reviewer_subject_ids_json=json.dumps(reviewers or []),
        subject_ids_json=json.dumps(subject_ids or ["*"]),
        asset_ids_json=json.dumps(asset_ids or ["*"]),
        account_ids_json=json.dumps(account_ids or ["*"]),
        command_group_ids_json=json.dumps(command_group_ids),
        is_active=is_active,
    )


def _request(command: str, **overrides: object) -> CommandDecisionRequest:
    data: dict[str, object] = {
        "subject": SubjectRef(id="user-1", type="user", tenant_id="tenant-a"),
        "resource": ResourceRef(id="asset-1", type="ssh_asset", tenant_id="tenant-a"),
        "account_id": "root",
        "command": command,
    }
    data.update(overrides)
    return CommandDecisionRequest(**data)  # type: ignore[arg-type]


# --- 默认放行：命令过滤是已授权会话上的叠加层 -----------------------------------


def test_command_allowed_by_default_when_no_acl() -> None:
    service = PolicyDecisionService()

    result = service.evaluate_command(_request("ls -la"))

    assert result.effect is CommandFilterEffect.ALLOW
    assert result.action == CommandFilterAction.ACCEPT
    assert result.reason_code == "COMMAND_ACCEPTED_BY_DEFAULT"
    assert result.matched_acl_id == ""
    assert result.audit_event_id.startswith("pde_")


def test_non_matching_command_falls_through_to_default_allow() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-danger"])],
        command_groups=[_group("grp-danger", ["rm", "shutdown"])],
    )

    result = service.evaluate_command(_request("ls -la"))

    assert result.effect is CommandFilterEffect.ALLOW
    assert result.matched_acl_id == ""


# --- reject 拦截 + 命令组匹配 ---------------------------------------------------


def test_reject_action_denies_matched_command() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-danger"], action=CommandFilterAction.REJECT)],
        command_groups=[_group("grp-danger", ["rm", "shutdown"])],
    )

    result = service.evaluate_command(_request("sudo rm -rf /"))

    assert result.effect is CommandFilterEffect.DENY
    assert result.action == CommandFilterAction.REJECT
    assert result.reason_code == "COMMAND_REJECT"
    assert result.matched_acl_id == "acl-1"
    assert result.matched_command_group_id == "grp-danger"


def test_literal_command_uses_word_boundary() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-rm"])],
        command_groups=[_group("grp-rm", ["rm"])],
    )

    # ``rm`` 作为独立命令词被拦截……
    assert service.evaluate_command(_request("rm file")).effect is CommandFilterEffect.DENY
    # ……但不误伤把 ``rm`` 作为子串的其它命令。
    assert (
        service.evaluate_command(_request("charmander --help")).effect
        is CommandFilterEffect.ALLOW
    )


# --- 优先级：小者优先 ----------------------------------------------------------


def test_lower_priority_number_wins() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[
            _acl("acl-allow", ["grp-rm"], action=CommandFilterAction.ACCEPT, priority=80),
            _acl("acl-deny", ["grp-rm"], action=CommandFilterAction.REJECT, priority=10),
        ],
        command_groups=[_group("grp-rm", ["rm"])],
    )

    result = service.evaluate_command(_request("rm -rf /data"))

    assert result.matched_acl_id == "acl-deny"
    assert result.effect is CommandFilterEffect.DENY


# --- review 动作返回复核人 ------------------------------------------------------


def test_review_action_returns_reviewers() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[
            _acl(
                "acl-1",
                ["grp-sensitive"],
                action=CommandFilterAction.REVIEW,
                reviewers=["mgr-1", "mgr-2"],
            )
        ],
        command_groups=[_group("grp-sensitive", ["drop"], match_type=CommandGroupMatchType.REGEX)],
    )

    result = service.evaluate_command(_request("drop table users"))

    assert result.effect is CommandFilterEffect.REVIEW
    assert result.reviewer_subject_ids == ["mgr-1", "mgr-2"]
    assert result.obligations["reviewer_subject_ids"] == ["mgr-1", "mgr-2"]


def test_notify_and_warn_action_carries_obligations() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[
            _acl("acl-1", ["grp-audit"], action=CommandFilterAction.NOTIFY_AND_WARN)
        ],
        command_groups=[_group("grp-audit", ["passwd"])],
    )

    result = service.evaluate_command(_request("passwd root"))

    assert result.effect is CommandFilterEffect.ALLOW
    assert result.obligations == {"warn": True, "notify": True}


# --- 正则命令组 ----------------------------------------------------------------


def test_regex_group_matches() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-re"])],
        command_groups=[
            _group("grp-re", [r"rm\s+-rf\s+/"], match_type=CommandGroupMatchType.REGEX)
        ],
    )

    assert service.evaluate_command(_request("rm -rf /")).effect is CommandFilterEffect.DENY
    assert service.evaluate_command(_request("rm -i file")).effect is CommandFilterEffect.ALLOW


def test_invalid_regex_is_safe_no_match() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-bad"])],
        command_groups=[_group("grp-bad", ["("], match_type=CommandGroupMatchType.REGEX)],
    )

    # 非法正则不得抛异常打断会话，安全地视为不匹配 → 默认放行。
    result = service.evaluate_command(_request("anything"))

    assert result.effect is CommandFilterEffect.ALLOW


# --- 选择器作用域 + 租户隔离 ----------------------------------------------------


def test_acl_scoped_to_other_subject_does_not_match() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-rm"], subject_ids=["user-2"])],
        command_groups=[_group("grp-rm", ["rm"])],
    )

    result = service.evaluate_command(_request("rm file"))

    assert result.effect is CommandFilterEffect.ALLOW


def test_acl_scoped_to_specific_asset_and_account() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[
            _acl("acl-1", ["grp-rm"], asset_ids=["asset-1"], account_ids=["root"])
        ],
        command_groups=[_group("grp-rm", ["rm"])],
    )

    assert service.evaluate_command(_request("rm file")).effect is CommandFilterEffect.DENY
    # 其它账号不在选择器内 → 不匹配。
    assert (
        service.evaluate_command(_request("rm file", account_id="deploy")).effect
        is CommandFilterEffect.ALLOW
    )


def test_other_tenant_acl_is_ignored() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-rm"], tenant_id="tenant-b")],
        command_groups=[_group("grp-rm", ["rm"], tenant_id="tenant-b")],
    )

    result = service.evaluate_command(_request("rm file"))

    assert result.effect is CommandFilterEffect.ALLOW


def test_tenant_mismatch_is_denied() -> None:
    service = PolicyDecisionService()

    result = service.evaluate_command(
        _request("ls", resource=ResourceRef(id="asset-1", type="ssh_asset", tenant_id="tenant-x"))
    )

    assert result.effect is CommandFilterEffect.DENY
    assert result.reason_code == "TENANT_MISMATCH"


def test_inactive_acl_and_group_are_ignored() -> None:
    service = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-rm"], is_active=False)],
        command_groups=[_group("grp-rm", ["rm"])],
    )
    assert service.evaluate_command(_request("rm file")).effect is CommandFilterEffect.ALLOW

    service_inactive_group = PolicyDecisionService(
        command_filter_acls=[_acl("acl-1", ["grp-rm"])],
        command_groups=[_group("grp-rm", ["rm"], is_active=False)],
    )
    assert (
        service_inactive_group.evaluate_command(_request("rm file")).effect
        is CommandFilterEffect.ALLOW
    )
