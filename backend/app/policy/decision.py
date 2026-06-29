"""Deny-by-default policy decision service."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

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

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules = rules or []

    def evaluate(self, request: PolicyDecisionRequest) -> PolicyDecisionResponse:
        trace: list[str] = [
            f"subject={request.subject.type}:{request.subject.id}",
            f"action={request.action}",
            f"resource={request.resource.type}:{request.resource.id}",
        ]

        preflight = self._preflight_deny(request, trace)
        if preflight is not None:
            return preflight

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
                obligations={"max_session_ttl_seconds": rule.max_session_ttl_seconds},
                ttl_seconds=rule.max_session_ttl_seconds,
            )

        trace.append("no_matching_policy")
        return self._deny("NO_MATCHING_POLICY", trace)

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
                return self._deny("APPROVAL_REQUIRED", trace)
            if request.approval.is_expired(datetime.now(UTC)):
                trace.append("approval_expired")
                return self._deny("APPROVAL_EXPIRED", trace)
            if not request.approval.is_approved_now(datetime.now(UTC)):
                trace.append(f"approval_not_approved:{request.approval.status}")
                return self._deny("APPROVAL_REQUIRED", trace)

        return None

    def _deny(self, reason_code: str, trace: list[str]) -> PolicyDecisionResponse:
        return self._response(
            decision=PolicyDecision.DENY,
            reason_code=reason_code,
            trace=trace,
            obligations={},
            ttl_seconds=0,
        )

    def _response(
        self,
        decision: PolicyDecision,
        reason_code: str,
        trace: list[str],
        obligations: dict[str, int],
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
