"""Deny-by-default policy decision service."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from datetime import UTC, datetime, time
from typing import Any
from uuid import uuid4

from app.models.acl import (
    CommandFilterAclModel,
    CommandFilterAction,
    CommandGroupMatchType,
    CommandGroupModel,
    DataMaskingMatchType,
    DataMaskingMethod,
    DataMaskingRuleModel,
)
from app.models.workflow import ApprovalPolicyModel
from app.policy.schemas import (
    CommandDecisionRequest,
    CommandDecisionResponse,
    CommandFilterEffect,
    MaskingRequest,
    MaskingResponse,
    PolicyDecision,
    PolicyDecisionRequest,
    PolicyDecisionResponse,
    PolicyRule,
)

# 命中命令过滤 ACL 后，动作到归一化效果的映射；未列出的动作按放行处理。
_COMMAND_ACTION_EFFECTS: dict[str, CommandFilterEffect] = {
    CommandFilterAction.REJECT: CommandFilterEffect.DENY,
    CommandFilterAction.REVIEW: CommandFilterEffect.REVIEW,
    CommandFilterAction.ACCEPT: CommandFilterEffect.ALLOW,
    CommandFilterAction.WARNING: CommandFilterEffect.ALLOW,
    CommandFilterAction.NOTICE: CommandFilterEffect.ALLOW,
    CommandFilterAction.NOTIFY_AND_WARN: CommandFilterEffect.ALLOW,
}


class PolicyDecisionService:
    """Central policy evaluator for authorization decisions.

    The service is intentionally deny-by-default. Route handlers and other
    bounded contexts should depend on this service instead of embedding policy
    logic in endpoints.
    """

    def __init__(
        self,
        rules: list[PolicyRule] | None = None,
        approval_policies: list[ApprovalPolicyModel] | None = None,
        command_filter_acls: list[CommandFilterAclModel] | None = None,
        command_groups: list[CommandGroupModel] | None = None,
        data_masking_rules: list[DataMaskingRuleModel] | None = None,
    ) -> None:
        self._rules = rules or []
        self._approval_policies = approval_policies or []
        self._command_filter_acls = command_filter_acls or []
        self._command_groups = command_groups or []
        self._data_masking_rules = data_masking_rules or []

    def evaluate(self, request: PolicyDecisionRequest) -> PolicyDecisionResponse:
        trace: list[str] = [
            f"subject={request.subject.type}:{request.subject.id}",
            f"action={request.action}",
            f"resource={request.resource.type}:{request.resource.id}",
        ]

        preflight = self._preflight_deny(request, trace)
        if preflight is not None:
            return preflight

        for policy in self._approval_policies:
            if not self._approval_policy_matches(policy, request):
                trace.append(f"approval_policy:{policy.id}:not_matched")
                continue
            if not self._approval_policy_dsl_includes(policy, request):
                trace.append(f"approval_policy:{policy.id}:dsl_excluded")
                continue
            if not self._approval_policy_rollout_includes(policy, request):
                trace.append(f"approval_policy:{policy.id}:rollout_excluded")
                continue

            trace.append(f"approval_policy:{policy.id}:matched")
            rule = self._rule_from_approval_policy(policy)
            deny = self._approval_policy_deny(policy, rule, request, trace)
            if deny is not None:
                return deny

            obligations = self._allow_obligations(rule, request)
            obligations["approval_policy_id"] = policy.id
            return self._response(
                decision=PolicyDecision.ALLOW,
                reason_code="POLICY_ALLOWED",
                trace=trace,
                obligations=obligations,
                ttl_seconds=policy.max_grant_ttl_seconds,
            )

        for rule in self._rules:
            if not rule.matches(request):
                trace.append(f"rule:{rule.id}:not_matched")
                continue

            trace.append(f"rule:{rule.id}:matched")
            deny = self._rule_deny(rule, request, trace)
            if deny is not None:
                return deny

            return self._response(
                decision=PolicyDecision.ALLOW,
                reason_code="POLICY_ALLOWED",
                trace=trace,
                obligations=self._allow_obligations(rule, request),
                ttl_seconds=rule.max_session_ttl_seconds,
            )

        trace.append("no_matching_policy")
        return self._deny("NO_MATCHING_POLICY", trace)

    def evaluate_command(self, request: CommandDecisionRequest) -> CommandDecisionResponse:
        """评估会话内单条命令是否被命令过滤 ACL 拦截 / 需复核。

        语义要点：命令过滤是叠加在**已授权会话**之上的精炼层（会话本身已由
        :meth:`evaluate` 走 deny-by-default 授权），因此与会话级判定相反——**无任何 ACL 命中
        时默认放行**（``accept``），否则每个 ACL 都要显式放行才可用，不可运维。

        判定：按 ``priority`` 升序（小者优先）取**首个**「选择器匹配且命令命中其任一命令组」
        的 ACL，其动作决定归一化效果（reject→deny / review→review / 其余→allow 并带告警/通知
        obligation）。不匹配的 ACL 让位给下一优先级。租户不一致直接拒绝。
        """

        trace: list[str] = [
            f"subject={request.subject.type}:{request.subject.id}",
            f"account={request.account_id}",
            f"resource={request.resource.type}:{request.resource.id}",
            f"command={request.command}",
        ]

        if request.subject.tenant_id != request.resource.tenant_id:
            trace.append("tenant_mismatch")
            return self._command_response(
                effect=CommandFilterEffect.DENY,
                action=CommandFilterAction.REJECT,
                reason_code="TENANT_MISMATCH",
                trace=trace,
            )

        groups_by_id = {
            group.id: group
            for group in self._command_groups
            if group.is_active and group.tenant_id == request.subject.tenant_id
        }

        candidates = sorted(
            (
                acl
                for acl in self._command_filter_acls
                if acl.is_active and acl.tenant_id == request.subject.tenant_id
            ),
            key=lambda acl: (acl.priority, acl.id),
        )

        for acl in candidates:
            if not self._command_acl_selectors_match(acl, request):
                trace.append(f"command_acl:{acl.id}:selector_not_matched")
                continue
            matched_group_id = self._command_matched_group(acl, request.command, groups_by_id)
            if matched_group_id is None:
                trace.append(f"command_acl:{acl.id}:command_not_in_groups")
                continue

            trace.append(f"command_acl:{acl.id}:matched:group={matched_group_id}")
            action = str(acl.action)
            effect = _COMMAND_ACTION_EFFECTS.get(action, CommandFilterEffect.ALLOW)
            reviewers = (
                self._load_json_list(acl.reviewer_subject_ids_json)
                if effect is CommandFilterEffect.REVIEW
                else []
            )
            return self._command_response(
                effect=effect,
                action=action,
                reason_code=f"COMMAND_{action.upper()}",
                trace=trace,
                matched_acl_id=acl.id,
                matched_command_group_id=matched_group_id,
                reviewer_subject_ids=reviewers,
                obligations=self._command_obligations(action, reviewers),
            )

        trace.append("no_matching_command_acl")
        return self._command_response(
            effect=CommandFilterEffect.ALLOW,
            action=CommandFilterAction.ACCEPT,
            reason_code="COMMAND_ACCEPTED_BY_DEFAULT",
            trace=trace,
        )

    def _command_acl_selectors_match(
        self, acl: CommandFilterAclModel, request: CommandDecisionRequest
    ) -> bool:
        return (
            self._id_in_selector(self._load_json_list(acl.subject_ids_json), request.subject.id)
            and self._id_in_selector(self._load_json_list(acl.asset_ids_json), request.resource.id)
            and self._id_in_selector(
                self._load_json_list(acl.account_ids_json), request.account_id
            )
        )

    def _command_matched_group(
        self,
        acl: CommandFilterAclModel,
        command: str,
        groups_by_id: dict[str, CommandGroupModel],
    ) -> str | None:
        for group_id in self._load_json_list(acl.command_group_ids_json):
            group = groups_by_id.get(group_id)
            if group is not None and self._command_matches_group(command, group):
                return group_id
        return None

    def _command_matches_group(self, command: str, group: CommandGroupModel) -> bool:
        patterns = self._load_json_list(group.patterns_json)
        is_regex = group.match_type == CommandGroupMatchType.REGEX
        for pattern in patterns:
            if not pattern:
                continue
            # 字面命令按词边界匹配（``rm`` 命中 ``sudo rm -rf`` 但不误伤 ``charmander``）；
            # 正则按 search 部分匹配。非法正则安全跳过，绝不让配置错误抛异常打断会话。
            expression = pattern if is_regex else rf"\b{re.escape(pattern)}\b"
            try:
                if re.search(expression, command):
                    return True
            except re.error:
                continue
        return False

    def _command_obligations(self, action: str, reviewers: list[str]) -> dict[str, object]:
        obligations: dict[str, object] = {}
        if action == CommandFilterAction.REVIEW:
            obligations["reviewer_subject_ids"] = reviewers
        if action in (CommandFilterAction.WARNING, CommandFilterAction.NOTIFY_AND_WARN):
            obligations["warn"] = True
        if action in (CommandFilterAction.NOTICE, CommandFilterAction.NOTIFY_AND_WARN):
            obligations["notify"] = True
        return obligations

    def _id_in_selector(self, selector_ids: list[str], value: str) -> bool:
        return "*" in selector_ids or value in selector_ids

    def _command_response(
        self,
        *,
        effect: CommandFilterEffect,
        action: str,
        reason_code: str,
        trace: list[str],
        matched_acl_id: str = "",
        matched_command_group_id: str = "",
        reviewer_subject_ids: list[str] | None = None,
        obligations: dict[str, object] | None = None,
    ) -> CommandDecisionResponse:
        return CommandDecisionResponse(
            effect=effect,
            action=str(action),
            reason_code=reason_code,
            matched_acl_id=matched_acl_id,
            matched_command_group_id=matched_command_group_id,
            reviewer_subject_ids=reviewer_subject_ids or [],
            explain_trace=trace,
            obligations=obligations or {},
            audit_event_id=f"pde_{uuid4().hex}",
        )

    def mask(self, request: MaskingRequest) -> MaskingResponse:
        """对一段会话文本按数据脱敏规则打码（命令输出 / 数据库结果）。

        与命令过滤的首个命中即止不同，脱敏**累计应用**所有命中选择器的活跃规则（按
        ``priority`` 升序，保证确定性），以覆盖多类敏感数据；每条规则对文本做全局替换。
        租户不一致时不应用任何规则（返回原文，trace 记 ``tenant_mismatch``），避免跨租户
        规则误伤。非法正则安全跳过，绝不因配置错误抛异常。
        """

        trace: list[str] = [
            f"subject={request.subject.type}:{request.subject.id}",
            f"account={request.account_id}",
            f"resource={request.resource.type}:{request.resource.id}",
        ]

        if request.subject.tenant_id != request.resource.tenant_id:
            trace.append("tenant_mismatch")
            return MaskingResponse(
                masked_text=request.text,
                redaction_count=0,
                applied_rule_ids=[],
                explain_trace=trace,
                audit_event_id=f"pde_{uuid4().hex}",
            )

        rules = sorted(
            (
                rule
                for rule in self._data_masking_rules
                if rule.is_active and rule.tenant_id == request.subject.tenant_id
            ),
            key=lambda rule: (rule.priority, rule.id),
        )

        text = request.text
        total_redactions = 0
        applied_rule_ids: list[str] = []
        for rule in rules:
            if not self._masking_selectors_match(rule, request):
                trace.append(f"masking_rule:{rule.id}:selector_not_matched")
                continue
            text, count = self._apply_masking_rule(text, rule)
            if count:
                total_redactions += count
                applied_rule_ids.append(rule.id)
                trace.append(f"masking_rule:{rule.id}:redacted:{count}")
            else:
                trace.append(f"masking_rule:{rule.id}:no_hit")

        return MaskingResponse(
            masked_text=text,
            redaction_count=total_redactions,
            applied_rule_ids=applied_rule_ids,
            explain_trace=trace,
            audit_event_id=f"pde_{uuid4().hex}",
        )

    def _masking_selectors_match(
        self, rule: DataMaskingRuleModel, request: MaskingRequest
    ) -> bool:
        return (
            self._id_in_selector(self._load_json_list(rule.subject_ids_json), request.subject.id)
            and self._id_in_selector(
                self._load_json_list(rule.asset_ids_json), request.resource.id
            )
            and self._id_in_selector(
                self._load_json_list(rule.account_ids_json), request.account_id
            )
        )

    def _apply_masking_rule(self, text: str, rule: DataMaskingRuleModel) -> tuple[str, int]:
        """对文本应用单条脱敏规则，返回 ``(新文本, 替换次数)``。

        ``keyword`` 类型对字面子串做转义后全局替换；``regex`` 直接用模式。两类都以每个匹配
        整体作为待打码值，交给 :meth:`_mask_value` 按 ``full`` / ``partial`` 打码。
        """

        is_regex = rule.match_type == DataMaskingMatchType.REGEX
        result = text
        count = 0
        for pattern in self._load_json_list(rule.patterns_json):
            if not pattern:
                continue
            expression = pattern if is_regex else re.escape(pattern)
            try:
                result, hits = re.subn(
                    expression, lambda match: self._mask_value(match.group(0), rule), result
                )
            except re.error:
                continue
            count += hits
        return result, count

    def _mask_value(self, value: str, rule: DataMaskingRuleModel) -> str:
        """按脱敏方式打码单个匹配值。"""

        if rule.mask_method == DataMaskingMethod.PARTIAL:
            keep_prefix = max(rule.keep_prefix, 0)
            keep_suffix = max(rule.keep_suffix, 0)
            if len(value) <= keep_prefix + keep_suffix:
                # 太短无法保留可见前后缀时整体打码，避免泄露原值。
                return "*" * len(value)
            middle = len(value) - keep_prefix - keep_suffix
            suffix = value[len(value) - keep_suffix :] if keep_suffix else ""
            return value[:keep_prefix] + "*" * middle + suffix
        return rule.placeholder

    def _approval_policy_matches(
        self, policy: ApprovalPolicyModel, request: PolicyDecisionRequest
    ) -> bool:
        if policy.tenant_id != request.subject.tenant_id or policy.tenant_id != request.resource.tenant_id:
            return False
        if policy.action_selector != "*" and policy.action_selector != request.action:
            return False

        selector = self._load_json_object(policy.resource_selector_json)
        if selector is None:
            return False

        supported_keys = {
            "asset_id",
            "resource_id",
            "resource_type",
            "type",
            "protocol",
            "organization_id",
            "team_id",
            "project_id",
        }
        if any(key not in supported_keys for key in selector):
            return False

        checks = {
            "asset_id": request.resource.id,
            "resource_id": request.resource.id,
            "resource_type": request.resource.type,
            "type": request.resource.type,
            "protocol": str(request.context.get("protocol", "")),
            "organization_id": request.resource.organization_id,
            "team_id": request.resource.team_id,
            "project_id": request.resource.project_id,
        }
        return all(self._selector_value_matches(expected, checks[key]) for key, expected in selector.items())

    def _approval_policy_dsl_includes(
        self, policy: ApprovalPolicyModel, request: PolicyDecisionRequest
    ) -> bool:
        conditions = self._load_json_object(policy.dsl_conditions_json)
        if conditions is None:
            return False
        if not conditions:
            return True
        return self._approval_policy_dsl_node_includes(conditions, request)

    def _approval_policy_dsl_node_includes(
        self, conditions: Any, request: PolicyDecisionRequest
    ) -> bool:
        if not isinstance(conditions, dict):
            return False
        if any(
            key
            not in {
                "context_equals",
                "context_in",
                "context_number_gt",
                "context_number_gte",
                "context_number_lt",
                "context_number_lte",
                "context_number_between",
                "context_number_not_between",
                "context_not_equals",
                "context_not_in",
                "context_exists",
                "context_contains",
                "context_not_contains",
                "context_starts_with",
                "context_ends_with",
                "context_matches_regex",
                "context_ip_in_cidr",
                "context_time_between",
                "context_time_not_between",
                "all",
                "any",
            }
            for key in conditions
        ):
            return False

        all_conditions = conditions.get("all", [])
        if not isinstance(all_conditions, list):
            return False
        if "all" in conditions and not all_conditions:
            return False
        if not all(
            self._approval_policy_dsl_node_includes(condition, request)
            for condition in all_conditions
        ):
            return False

        any_conditions = conditions.get("any", [])
        if not isinstance(any_conditions, list):
            return False
        if "any" in conditions and not any_conditions:
            return False
        if any_conditions and not any(
            self._approval_policy_dsl_node_includes(condition, request)
            for condition in any_conditions
        ):
            return False

        context_equals = conditions.get("context_equals", {})
        if not isinstance(context_equals, dict):
            return False
        if not all(
            self._selector_value_matches(expected, str(request.context.get(key, "")))
            for key, expected in context_equals.items()
        ):
            return False

        context_in = conditions.get("context_in", {})
        if not isinstance(context_in, dict):
            return False
        if not all(
            isinstance(expected_values, list)
            and str(request.context.get(key, "")) in {str(value) for value in expected_values}
            for key, expected_values in context_in.items()
        ):
            return False

        context_number_gt = conditions.get("context_number_gt", {})
        if not isinstance(context_number_gt, dict):
            return False
        if not all(
            self._context_number_satisfies(request.context.get(key), minimum, operator="gt")
            for key, minimum in context_number_gt.items()
        ):
            return False

        context_number_gte = conditions.get("context_number_gte", {})
        if not isinstance(context_number_gte, dict):
            return False
        if not all(
            self._context_number_satisfies(request.context.get(key), minimum, operator="gte")
            for key, minimum in context_number_gte.items()
        ):
            return False

        context_number_lt = conditions.get("context_number_lt", {})
        if not isinstance(context_number_lt, dict):
            return False
        if not all(
            self._context_number_satisfies(request.context.get(key), maximum, operator="lt")
            for key, maximum in context_number_lt.items()
        ):
            return False

        context_number_lte = conditions.get("context_number_lte", {})
        if not isinstance(context_number_lte, dict):
            return False
        if not all(
            self._context_number_satisfies(request.context.get(key), maximum, operator="lte")
            for key, maximum in context_number_lte.items()
        ):
            return False

        context_number_between = conditions.get("context_number_between", {})
        if not isinstance(context_number_between, dict):
            return False
        if not all(
            self._context_number_between(request.context.get(key), expected_range)
            for key, expected_range in context_number_between.items()
        ):
            return False

        context_number_not_between = conditions.get("context_number_not_between", {})
        if not isinstance(context_number_not_between, dict):
            return False
        if not all(
            self._context_number_not_between(request.context.get(key), expected_range)
            for key, expected_range in context_number_not_between.items()
        ):
            return False

        context_not_equals = conditions.get("context_not_equals", {})
        if not isinstance(context_not_equals, dict):
            return False
        if not all(
            not self._selector_value_matches(disallowed, str(request.context.get(key, "")))
            for key, disallowed in context_not_equals.items()
        ):
            return False

        context_not_in = conditions.get("context_not_in", {})
        if not isinstance(context_not_in, dict):
            return False
        if not all(
            isinstance(disallowed_values, list)
            and str(request.context.get(key, ""))
            not in {str(value) for value in disallowed_values}
            for key, disallowed_values in context_not_in.items()
        ):
            return False

        context_exists = conditions.get("context_exists", [])
        if not isinstance(context_exists, list):
            return False
        if "context_exists" in conditions and not context_exists:
            return False
        if not all(
            isinstance(key, str) and key in request.context and request.context[key] is not None
            for key in context_exists
        ):
            return False

        context_contains = conditions.get("context_contains", {})
        if not isinstance(context_contains, dict):
            return False
        if not all(
            self._context_string_contains(request.context.get(key), expected)
            for key, expected in context_contains.items()
        ):
            return False

        context_not_contains = conditions.get("context_not_contains", {})
        if not isinstance(context_not_contains, dict):
            return False
        if not all(
            self._context_string_not_contains(request.context.get(key), disallowed)
            for key, disallowed in context_not_contains.items()
        ):
            return False

        context_starts_with = conditions.get("context_starts_with", {})
        if not isinstance(context_starts_with, dict):
            return False
        if not all(
            self._context_string_starts_with(request.context.get(key), expected)
            for key, expected in context_starts_with.items()
        ):
            return False

        context_ends_with = conditions.get("context_ends_with", {})
        if not isinstance(context_ends_with, dict):
            return False
        if not all(
            self._context_string_ends_with(request.context.get(key), expected)
            for key, expected in context_ends_with.items()
        ):
            return False

        context_matches_regex = conditions.get("context_matches_regex", {})
        if not isinstance(context_matches_regex, dict):
            return False
        if not all(
            self._context_string_matches_regex(request.context.get(key), expected)
            for key, expected in context_matches_regex.items()
        ):
            return False

        context_ip_in_cidr = conditions.get("context_ip_in_cidr", {})
        if not isinstance(context_ip_in_cidr, dict):
            return False
        if not all(
            self._context_ip_in_cidr(request.context.get(key), expected_networks)
            for key, expected_networks in context_ip_in_cidr.items()
        ):
            return False

        context_time_between = conditions.get("context_time_between", {})
        if not isinstance(context_time_between, dict):
            return False
        if not all(
            self._context_time_between(request.context.get(key), expected_window)
            for key, expected_window in context_time_between.items()
        ):
            return False

        context_time_not_between = conditions.get("context_time_not_between", {})
        if not isinstance(context_time_not_between, dict):
            return False
        return all(
            self._context_time_not_between(request.context.get(key), expected_window)
            for key, expected_window in context_time_not_between.items()
        )

    def _context_number_satisfies(self, actual: Any, expected: Any, *, operator: str) -> bool:
        actual_number = self._coerce_finite_number(actual)
        expected_number = self._coerce_finite_number(expected)
        if actual_number is None or expected_number is None:
            return False
        if operator == "gt":
            return actual_number > expected_number
        if operator == "gte":
            return actual_number >= expected_number
        if operator == "lt":
            return actual_number < expected_number
        if operator == "lte":
            return actual_number <= expected_number
        return False

    def _context_number_between(self, actual: Any, expected_range: Any) -> bool:
        if not isinstance(expected_range, dict):
            return False
        actual_number = self._coerce_finite_number(actual)
        minimum = self._coerce_finite_number(expected_range.get("min"))
        maximum = self._coerce_finite_number(expected_range.get("max"))
        if actual_number is None or minimum is None or maximum is None:
            return False
        if minimum > maximum:
            return False
        return minimum <= actual_number <= maximum

    def _context_number_not_between(self, actual: Any, expected_range: Any) -> bool:
        if not isinstance(expected_range, dict):
            return False
        actual_number = self._coerce_finite_number(actual)
        minimum = self._coerce_finite_number(expected_range.get("min"))
        maximum = self._coerce_finite_number(expected_range.get("max"))
        if actual_number is None or minimum is None or maximum is None:
            return False
        if minimum > maximum:
            return False
        return not minimum <= actual_number <= maximum

    def _context_string_contains(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return expected in actual

    def _context_string_not_contains(self, actual: Any, disallowed: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(disallowed, str) or not disallowed:
            return False
        return disallowed not in actual

    def _context_string_starts_with(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return actual.startswith(expected)

    def _context_string_ends_with(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return actual.endswith(expected)

    def _context_string_matches_regex(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        try:
            return re.fullmatch(expected, actual) is not None
        except re.error:
            return False

    def _context_ip_in_cidr(self, actual: Any, expected_networks: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected_networks, list):
            return False
        if not actual or not expected_networks:
            return False
        try:
            ip_address = ipaddress.ip_address(actual)
            networks = [
                ipaddress.ip_network(network, strict=False)
                for network in expected_networks
                if isinstance(network, str) and network
            ]
        except ValueError:
            return False
        if len(networks) != len(expected_networks):
            return False
        return any(ip_address in network for network in networks)

    def _context_time_between(self, actual: Any, expected_window: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected_window, dict):
            return False
        actual_time = self._parse_context_time(actual)
        start_time = self._parse_context_time(expected_window.get("start"))
        end_time = self._parse_context_time(expected_window.get("end"))
        if actual_time is None or start_time is None or end_time is None:
            return False
        return self._time_in_window(actual_time, start_time, end_time)

    def _context_time_not_between(self, actual: Any, expected_window: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected_window, dict):
            return False
        actual_time = self._parse_context_time(actual)
        start_time = self._parse_context_time(expected_window.get("start"))
        end_time = self._parse_context_time(expected_window.get("end"))
        if actual_time is None or start_time is None or end_time is None:
            return False
        return not self._time_in_window(actual_time, start_time, end_time)

    def _time_in_window(self, actual_time: time, start_time: time, end_time: time) -> bool:
        if start_time <= end_time:
            return start_time <= actual_time <= end_time
        return actual_time >= start_time or actual_time <= end_time

    def _parse_context_time(self, value: Any) -> time | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return time.fromisoformat(value)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).time()
        except ValueError:
            return None

    def _coerce_finite_number(self, value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int | float | str):
            try:
                number = float(value)
            except ValueError:
                return None
            if math.isfinite(number):
                return number
        return None

    def _approval_policy_rollout_includes(
        self, policy: ApprovalPolicyModel, request: PolicyDecisionRequest
    ) -> bool:
        if policy.rollout_percentage >= 100:
            return True
        if policy.rollout_percentage <= 0:
            return False
        seed = f"{policy.id}:{request.subject.tenant_id}:{request.subject.id}:{request.resource.id}"
        bucket = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < policy.rollout_percentage

    def _rule_from_approval_policy(self, policy: ApprovalPolicyModel) -> PolicyRule:
        selector = self._load_json_object(policy.resource_selector_json) or {}
        asset_id = str(selector.get("asset_id") or selector.get("resource_id") or "*")
        return PolicyRule(
            id=f"approval_policy:{policy.id}",
            subject_ids=["*"],
            actions=[policy.action_selector],
            resource_ids=[asset_id],
            tenant_id=policy.tenant_id,
            require_mfa=policy.require_mfa_for_requester,
            require_approval=True,
            organization_ids=self._selector_ids(selector.get("organization_id")),
            team_ids=self._selector_ids(selector.get("team_id")),
            project_ids=self._selector_ids(selector.get("project_id")),
            max_session_ttl_seconds=policy.max_grant_ttl_seconds,
        )

    def _approval_policy_deny(
        self,
        policy: ApprovalPolicyModel,
        rule: PolicyRule,
        request: PolicyDecisionRequest,
        trace: list[str],
    ) -> PolicyDecisionResponse | None:
        if rule.require_mfa and not request.mfa_verified:
            trace.append("mfa_required_but_missing")
            return self._deny("MFA_REQUIRED", trace)

        if request.approval is None:
            trace.append("approval_required_but_missing")
            return self._deny(
                "APPROVAL_REQUIRED",
                trace,
                obligations={
                    "workflow_required": True,
                    "approval_policy_id": policy.id,
                    "approval_use_type": rule.approval_use_type,
                    "approval_max_uses": rule.approval_max_uses,
                    "approver_subject_ids": self._load_json_list(
                        policy.approver_subject_ids_json
                    ),
                    "approver_mode": policy.approver_mode,
                    "require_mfa_for_approver": policy.require_mfa_for_approver,
                    "max_grant_ttl_seconds": policy.max_grant_ttl_seconds,
                    "risk_level": policy.risk_level,
                },
            )

        return self._rule_deny(rule, request, trace)

    def _preflight_deny(
        self,
        request: PolicyDecisionRequest,
        trace: list[str],
    ) -> PolicyDecisionResponse | None:
        if not request.subject.id:
            return self._deny("UNKNOWN_SUBJECT", trace)
        if not request.resource.id:
            return self._deny("UNKNOWN_RESOURCE", trace)
        if not request.action:
            return self._deny("MISSING_ACTION", trace)
        if request.subject.tenant_id != request.resource.tenant_id:
            trace.append("tenant_mismatch")
            return self._deny("TENANT_MISMATCH", trace)
        if not request.connector_trusted:
            trace.append("connector_not_trusted")
            return self._deny("CONNECTOR_NOT_TRUSTED", trace)
        return None

    def _rule_deny(
        self,
        rule: PolicyRule,
        request: PolicyDecisionRequest,
        trace: list[str],
    ) -> PolicyDecisionResponse | None:
        if rule.require_mfa and not request.mfa_verified:
            trace.append("mfa_required_but_missing")
            return self._deny("MFA_REQUIRED", trace)

        if rule.require_approval:
            if request.approval is None:
                trace.append("approval_required_but_missing")
                return self._deny(
                    "APPROVAL_REQUIRED",
                    trace,
                    obligations={
                        "workflow_required": True,
                        "approval_use_type": rule.approval_use_type,
                        "approval_max_uses": rule.approval_max_uses,
                    },
                )
            if request.approval.is_expired(datetime.now(UTC)):
                trace.append("approval_expired")
                return self._deny("APPROVAL_EXPIRED", trace)
            if not request.approval.is_approved_now(datetime.now(UTC)):
                trace.append(f"approval_not_approved:{request.approval.status}")
                return self._deny("APPROVAL_REQUIRED", trace)
            if not request.approval.grant_id or not request.approval.workflow_request_id:
                trace.append("approval_grant_identity_missing")
                return self._deny("APPROVAL_GRANT_REQUIRED", trace)
            if not self._approval_constraints_match(request):
                trace.append("approval_constraints_mismatch")
                return self._deny("APPROVAL_CONSTRAINT_MISMATCH", trace)

        return None

    def _approval_constraints_match(self, request: PolicyDecisionRequest) -> bool:
        if request.approval is None:
            return False
        constraints = request.approval.constraints
        context_account_id = request.context.get("account_id")
        context_protocol = request.context.get("protocol")
        if not context_account_id or not context_protocol:
            return False

        expected: dict[str, str] = {
            "subject_id": request.subject.id,
            "asset_id": request.resource.id,
            "account_id": str(context_account_id),
            "protocol": str(context_protocol),
            "action": request.action,
        }

        return all(str(constraints.get(key, "")) == value for key, value in expected.items())

    def _allow_obligations(
        self, rule: PolicyRule, request: PolicyDecisionRequest
    ) -> dict[str, object]:
        obligations: dict[str, object] = {"max_session_ttl_seconds": rule.max_session_ttl_seconds}
        if rule.require_approval and request.approval is not None:
            constraints = request.approval.constraints
            obligations.update(
                {
                    "workflow_required": True,
                    "jit_grant_id": request.approval.grant_id,
                    "workflow_request_id": request.approval.workflow_request_id,
                    "grant_usage": constraints.get("usage", rule.approval_use_type),
                    "grant_max_uses": constraints.get("max_uses", rule.approval_max_uses),
                    "grant_used_count": constraints.get("used_count", 0),
                }
            )
        return obligations

    def _load_json_object(self, raw: str) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _load_json_list(self, raw: str) -> list[str]:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    def _selector_ids(self, value: Any) -> list[str]:
        if value in (None, "", "*"):
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)]

    def _selector_value_matches(self, expected: Any, actual: str) -> bool:
        if expected in (None, "", "*"):
            return True
        if isinstance(expected, list):
            return "*" in expected or actual in {str(item) for item in expected}
        return str(expected) == actual

    def _deny(
        self,
        reason_code: str,
        trace: list[str],
        obligations: dict[str, object] | None = None,
    ) -> PolicyDecisionResponse:
        return self._response(
            decision=PolicyDecision.DENY,
            reason_code=reason_code,
            trace=trace,
            obligations=obligations or {},
            ttl_seconds=0,
        )

    def _response(
        self,
        decision: PolicyDecision,
        reason_code: str,
        trace: list[str],
        obligations: dict[str, object],
        ttl_seconds: int,
    ) -> PolicyDecisionResponse:
        return PolicyDecisionResponse(
            decision=decision,
            reason_code=reason_code,
            explain_trace=trace,
            obligations=obligations,
            ttl_seconds=ttl_seconds,
            audit_event_id=f"pde_{uuid4().hex}",
        )
