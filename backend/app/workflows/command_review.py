"""#t74 command_review TicketFlow helpers and one-shot command allow grants."""
from __future__ import annotations

from typing import Any

COMMAND_REVIEW_ACTION = "command.review"
COMMAND_ALLOW_ACTION = "command.allow"
COMMAND_REVIEW_PENDING = "command_review_pending"
COMMAND_REVIEW_TTL_SECONDS = 600  # 10 minutes

USER_MSG_PENDING = "命令待复核"
USER_MSG_APPROVED_RETRY = "已通过，请重新执行该命令"
USER_MSG_REJECTED = "命令未通过复核"
USER_MSG_REJECT_CONFIRM = "确定拒绝？该命令将不会执行。"


def is_command_review_request(request: Any) -> bool:
    action = str(getattr(request, "action", "") or "")
    if action == COMMAND_REVIEW_ACTION:
        return True
    metadata = getattr(request, "metadata", None) or {}
    return isinstance(metadata, dict) and metadata.get("ticket_type") == "command_review"


def command_from_request(request: Any) -> str:
    metadata = getattr(request, "metadata", None) or {}
    if isinstance(metadata, dict):
        command = metadata.get("command")
        if isinstance(command, str):
            return command
    return ""


def build_command_review_metadata(
    *,
    command: str,
    session_id: str | None,
    reviewer_subject_ids: list[str] | None = None,
    protocol: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    return {
        "ticket_type": "command_review",
        "command": command,
        "session_id": session_id or "",
        "reviewer_subject_ids": list(reviewer_subject_ids or []),
        "protocol": protocol,
        "account_id": account_id,
    }
