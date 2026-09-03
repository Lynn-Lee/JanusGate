"""#t71 数据库通道共享错误与工具。"""

from __future__ import annotations

_OUTPUT_EXCERPT_LIMIT = 4096


class DbChannelError(RuntimeError):
    """数据库代理通道错误，携带稳定错误码且不承载凭据上下文。"""

    def __init__(self, code: str, detail: str, *, audit_event_id: str = "") -> None:
        self.code = code
        self.detail = detail
        self.audit_event_id = audit_event_id
        super().__init__(f"{code}: {detail}")


def merge_output_excerpt(stdout: str, stderr: str) -> str:
    if not stderr:
        return stdout[:_OUTPUT_EXCERPT_LIMIT]
    if not stdout:
        return stderr[:_OUTPUT_EXCERPT_LIMIT]
    err_budget = min(len(stderr), _OUTPUT_EXCERPT_LIMIT // 2)
    out_budget = _OUTPUT_EXCERPT_LIMIT - err_budget
    return stdout[:out_budget] + stderr[:err_budget]
