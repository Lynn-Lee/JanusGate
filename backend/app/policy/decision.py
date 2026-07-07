"""Deny-by-default policy decision service."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.workflow import ApprovalPolicyModel
from app.policy.schemas import (
    PolicyDecision,
    PolicyDecisionRequest,
    PolicyDecisionResponse,
    PolicyRule,
)


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
    ) -> None:
        self._rules = rules or []
        self._approval_policies = approval_policies or []

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
                "context_number_gte",
                "context_number_lte",
                "context_not_equals",
                "context_not_in",
                "context_exists",
                "context_contains",
                "context_starts_with",
                "context_ends_with",
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

        context_number_gte = conditions.get("context_number_gte", {})
        if not isinstance(context_number_gte, dict):
            return False
        if not all(
            self._context_number_satisfies(request.context.get(key), minimum, operator="gte")
            for key, minimum in context_number_gte.items()
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
        return all(
            self._context_string_ends_with(request.context.get(key), expected)
            for key, expected in context_ends_with.items()
        )

    def _context_number_satisfies(self, actual: Any, expected: Any, *, operator: str) -> bool:
        actual_number = self._coerce_finite_number(actual)
        expected_number = self._coerce_finite_number(expected)
        if actual_number is None or expected_number is None:
            return False
        if operator == "gte":
            return actual_number >= expected_number
        if operator == "lte":
            return actual_number <= expected_number
        return False

    def _context_string_contains(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return expected in actual

    def _context_string_starts_with(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return actual.startswith(expected)

    def _context_string_ends_with(self, actual: Any, expected: Any) -> bool:
        if not isinstance(actual, str) or not isinstance(expected, str) or not expected:
            return False
        return actual.endswith(expected)

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
