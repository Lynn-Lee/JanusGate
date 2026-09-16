"""#t71 数据库通道共享错误与输出摘要工具。"""

from __future__ import annotations

_OUTPUT_EXCERPT_LIMIT = 4096


class DbChannelError(RuntimeError):
    """数据库代理通道错误，携带稳定错误码且不承载凭据上下文。

    :param code: 稳定的机器可读错误码，用于审计与安全回归断言。
    :param detail: 面向运维的人类可读描述，不得包含密码等敏感信息。
    """

    def __init__(self, code: str, detail: str, *, audit_event_id: str = "") -> None:
        self.code = code
        self.detail = detail
        self.audit_event_id = audit_event_id
        super().__init__(f"{code}: {detail}")


def merge_output_excerpt(stdout: str, stderr: str) -> str:
    """合并 stdout/stderr 为命令事件摘要，stderr 保留独立预算。"""

    if not stderr:
        return stdout[:_OUTPUT_EXCERPT_LIMIT]
    if not stdout:
        return stderr[:_OUTPUT_EXCERPT_LIMIT]
    err_budget = min(len(stderr), _OUTPUT_EXCERPT_LIMIT // 2)
    out_budget = _OUTPUT_EXCERPT_LIMIT - err_budget
    return stdout[:out_budget] + stderr[:err_budget]
