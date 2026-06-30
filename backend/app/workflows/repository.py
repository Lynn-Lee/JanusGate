"""Workflow/JIT repository interfaces and in-memory test implementation."""
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.models.workflow import (
    JitGrantModel,
    JitGrantStatus,
    WorkflowRequestModel,
    WorkflowRequestStatus,
)


class InMemoryWorkflowRepository:
    """Behavior-compatible repository used by unit tests and service fakes.

    Production code can provide an AsyncSession-backed implementation with the same method contract.
    """

    def __init__(self) -> None:
        self._requests: dict[str, WorkflowRequestModel] = {}
        self._grants: dict[str, JitGrantModel] = {}

    def create_request(
        self,
        *,
        tenant_id: str,
        requester_id: str,
        requester_username: str,
        resource_type: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        reason: str,
        requested_ttl_seconds: int,
        metadata: dict[str, Any],
    ) -> WorkflowRequestModel:
        now = datetime.now(UTC)
        request = WorkflowRequestModel(
            id=f"wr_{uuid4().hex}",
            tenant_id=tenant_id,
            requester_id=requester_id,
            requester_username=requester_username,
            resource_type=resource_type,
            asset_id=asset_id,
            account_id=account_id,
            protocol=protocol,
            action=action,
            reason=reason,
            requested_ttl_seconds=requested_ttl_seconds,
            status=WorkflowRequestStatus.draft,
            created_at=now,
            metadata_json=json.dumps(metadata, sort_keys=True),
        )
        self._requests[request.id] = request
        return request

    def get_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel | None:
        request = self._requests.get(request_id)
        if request is None or request.tenant_id != tenant_id:
            return None
        return request

    def list_requests(
        self, *, tenant_id: str, requester_id: str | None = None
    ) -> list[WorkflowRequestModel]:
        return [
            request
            for request in self._requests.values()
            if request.tenant_id == tenant_id
            and (requester_id is None or request.requester_id == requester_id)
        ]

    def submit_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = self._require_request(request_id, tenant_id=tenant_id)
        if request.status != WorkflowRequestStatus.draft:
            raise ValueError("Only draft workflow requests can be submitted")
        request.status = WorkflowRequestStatus.pending
        request.submitted_at = datetime.now(UTC)
        return request

    def approve_request(
        self,
        request_id: str,
        *,
        tenant_id: str,
        approver_id: str,
        approver_username: str,
        decision_reason: str,
        grant_ttl_seconds: int,
        max_session_ttl_seconds: int,
        constraints: dict[str, Any],
    ) -> JitGrantModel:
        request = self._require_request(request_id, tenant_id=tenant_id)
        if request.status != WorkflowRequestStatus.pending:
            raise ValueError("Only pending workflow requests can be approved")
        now = datetime.now(UTC)
        request.status = WorkflowRequestStatus.approved
        request.decided_at = now
        request.decision_reason = decision_reason
        request.approver_id = approver_id
        request.approver_username = approver_username
        request.expires_at = now + timedelta(seconds=grant_ttl_seconds)
        grant = JitGrantModel(
            id=f"jg_{uuid4().hex}",
            tenant_id=tenant_id,
            workflow_request_id=request.id,
            subject_id=request.requester_id,
            asset_id=request.asset_id,
            account_id=request.account_id,
            protocol=request.protocol,
            action=request.action,
            status=JitGrantStatus.active,
            issued_at=now,
            expires_at=request.expires_at,
            max_session_ttl_seconds=max_session_ttl_seconds,
            constraints_json=json.dumps(constraints, sort_keys=True),
        )
        self._grants[grant.id] = grant
        return grant

    def find_active_grant(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        asset_id: str,
        account_id: str,
        protocol: str,
        action: str,
        now: datetime,
    ) -> JitGrantModel | None:
        for grant in self._grants.values():
            if (
                grant.tenant_id == tenant_id
                and grant.subject_id == subject_id
                and grant.asset_id == asset_id
                and grant.account_id == account_id
                and grant.protocol == protocol
                and grant.action == action
                and grant.status == JitGrantStatus.active
                and grant.expires_at > now
            ):
                return grant
        return None

    def _require_request(self, request_id: str, *, tenant_id: str) -> WorkflowRequestModel:
        request = self.get_request(request_id, tenant_id=tenant_id)
        if request is None:
            raise ValueError("Workflow request not found")
        return request
