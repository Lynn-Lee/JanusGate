"""SSH 主机密钥扫描（采集 → 审批 → 固定）。

严格主机密钥校验（P0#17）要求 :class:`~app.connectors.ssh_channel.SshTarget` 预置一个
可信主机公钥，但这个 key 必须先有一条**安全的获取途径**。本模块提供堡垒机常见的
「采集主机密钥」步骤：连接目标仅完成密钥交换、取回其主机公钥即断开，**不进行用户认证、
不信任该 key**。采集结果交由管理员核对指纹并审批后，其 ``public_key`` 即可作为
``SshTarget.trusted_host_key`` 固定，从而启用严格校验，形成完整闭环。

采集阶段同样强制现代 KEX 与主机密钥算法：只提供弱算法的服务器会在协商阶段被拒绝，
不会被采集固定（与 :mod:`app.connectors.ssh_channel` 的 P0#7 约束一致）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncssh

from app.connectors.ssh_channel import (
    MODERN_HOST_KEY_ALGS,
    MODERN_KEX_ALGS,
    SshChannelError,
)


@dataclass(frozen=True)
class HostKeyScan:
    """一次主机密钥采集的结果，供管理员审批与固定。

    :param host: 被采集的主机名或 IP。
    :param port: 被采集的端口。
    :param key_type: 主机密钥算法（如 ``ssh-ed25519``）。
    :param public_key: 主机公钥单行（``"ssh-ed25519 AAAA..."``），审批后可直接作为
        :attr:`SshTarget.trusted_host_key` 固定。
    :param fingerprint: 主机公钥的 ``SHA256:`` 指纹，供管理员带外核对。
    """

    host: str
    port: int
    key_type: str
    public_key: str
    fingerprint: str


async def scan_host_key(host: str, port: int = 22, *, connect_timeout: float = 10.0) -> HostKeyScan:
    """采集目标主机的 SSH 主机公钥，用于人工审批与固定。

    仅完成密钥交换取回主机公钥即断开，不进行用户认证，也不信任所采集的 key。

    :param host: 目标主机名或 IP。
    :param port: 目标端口。
    :param connect_timeout: 采集超时秒数。
    :returns: 采集结果 :class:`HostKeyScan`。
    :raises SshChannelError: 采集超时（``SSH_HOST_KEY_SCAN_TIMEOUT``）或失败
        （``SSH_HOST_KEY_SCAN_FAILED``，含目标只提供弱算法、拒绝连接、未返回主机密钥等）。

    安全提示：本函数会向传入的 ``host`` 发起连接，调用方必须先用资产白名单 / SSRF 防护
    校验 ``host``，不得直接把不可信输入透传进来。
    """

    try:
        key = await asyncio.wait_for(
            asyncssh.get_server_host_key(
                host,
                port,
                kex_algs=MODERN_KEX_ALGS,
                server_host_key_algs=MODERN_HOST_KEY_ALGS,
            ),
            timeout=connect_timeout,
        )
    except TimeoutError as exc:
        raise SshChannelError("SSH_HOST_KEY_SCAN_TIMEOUT", "host key scan timed out") from exc
    except (asyncssh.Error, OSError) as exc:
        raise SshChannelError("SSH_HOST_KEY_SCAN_FAILED", str(exc)) from exc
    if key is None:
        raise SshChannelError(
            "SSH_HOST_KEY_SCAN_FAILED",
            "server presented no acceptable host key under modern algorithms",
        )
    return HostKeyScan(
        host=host,
        port=port,
        key_type=key.get_algorithm(),
        public_key=key.export_public_key().decode().strip(),
        fingerprint=key.get_fingerprint(),
    )
