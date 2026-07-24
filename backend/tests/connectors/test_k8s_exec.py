"""#t72 M3：真实 K8s ``exec`` 通道端到端与安全回归测试。

全部用例基于 ``websockets`` 进程内 wss 服务器（自签证书 + ``v4.channel.k8s.io`` 帧），
无外部集群依赖、可在 CI 中确定性运行。三条安全约束（namespace 作用域强制、TLS 强校验、
凭据仅内存不经 URL/命令行）逐条有对应断言。
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from websockets import Subprotocol
from websockets.asyncio.server import Server, ServerConnection, serve

from app.connectors.k8s_exec import (
    V4_CHANNEL_SUBPROTOCOL,
    K8sChannelError,
    K8sCredential,
    K8sExecChannel,
    K8sTarget,
    NamespaceScope,
    _parse_exit_status,
)
from app.connectors.ssh_channel import CommandEvent


@dataclass
class _RecordingSink:
    """内存命令事件下游，替代 #t46 HTTP 管线用于断言。"""

    events: list[CommandEvent] = field(default_factory=list)

    async def emit(self, event: CommandEvent) -> None:
        self.events.append(event)


def _self_signed_cert() -> tuple[str, str]:
    """生成一张 ``127.0.0.1`` 自签证书，返回 ``(cert_pem, key_pem)``。

    自签叶证书同时用作服务端证书与客户端信任锚（``server_ca``），SAN 含 IP 与
    ``localhost``，以便客户端在 ``check_hostname`` 下严格校验通过。
    """

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


@dataclass
class _CapturedRequest:
    path: str
    authorization: str | None
    subprotocol: str | None


@dataclass
class _RunningServer:
    server: Server
    host: str
    port: int
    ca_pem: str
    captured: list[_CapturedRequest]


async def _exec_handler(connection: ServerConnection) -> None:
    """模拟 API Server 的 ``pods/exec``：回显命令并在 error 通道回传退出状态。

    命令原文（argv 末元素）中含 ``fail`` 时回传非零退出码 7，含 ``stderr`` 时额外写一帧
    stderr，否则回传 ``Success``（退出码 0）。
    """

    request = connection.request
    assert request is not None
    query = parse_qs(urlparse(request.path).query)
    argv = query.get("command", [])
    user_cmd = argv[-1] if argv else ""

    await connection.send(bytes([1]) + f"executed:{user_cmd}".encode())
    if "stderr" in user_cmd:
        await connection.send(bytes([2]) + b"warning-on-stderr")
    if "fail" in user_cmd:
        status: dict[str, object] = {
            "metadata": {},
            "status": "Failure",
            "reason": "NonZeroExitCode",
            "details": {"causes": [{"reason": "ExitCode", "message": "7"}]},
        }
    else:
        status = {"metadata": {}, "status": "Success"}
    await connection.send(bytes([3]) + json.dumps(status).encode())


@pytest.fixture
async def k8s_server(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_RunningServer]:
    cert_pem, key_pem = _self_signed_cert()
    cert_dir = tmp_path_factory.mktemp("k8s-tls")
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"
    cert_path.write_text(cert_pem)
    key_path.write_text(key_pem)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(str(cert_path), str(key_path))

    captured: list[_CapturedRequest] = []

    async def _tracking_handler(connection: ServerConnection) -> None:
        request = connection.request
        assert request is not None
        captured.append(
            _CapturedRequest(
                path=request.path,
                authorization=request.headers.get("Authorization"),
                subprotocol=connection.subprotocol,
            )
        )
        await _exec_handler(connection)

    server = await serve(
        _tracking_handler,
        "127.0.0.1",
        0,
        ssl=ssl_context,
        subprotocols=[Subprotocol(V4_CHANNEL_SUBPROTOCOL)],
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield _RunningServer(
            server=server,
            host="127.0.0.1",
            port=port,
            ca_pem=cert_pem,
            captured=captured,
        )
    finally:
        server.close()
        await server.wait_closed()


def _target(running: _RunningServer, *, namespace: str = "team-a", ca: str | None = "__ca__") -> K8sTarget:
    return K8sTarget(
        api_server=f"https://{running.host}:{running.port}",
        namespace=namespace,
        pod="web-0",
        container="app",
        server_ca=running.ca_pem if ca == "__ca__" else ca,
    )


def _scope(*namespaces: str) -> NamespaceScope:
    return NamespaceScope(namespaces=frozenset(namespaces))


# --- 端到端：真实 exec 通道、命令执行、命令事件投递 -------------------------------


async def test_end_to_end_runs_command_and_emits_event(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server), K8sCredential(token="sa-token"), _scope("team-a")
    )
    sink = _RecordingSink()

    async with channel:
        event = await channel.run_command("whoami", sink, sequence=0)

    assert event.exit_code == 0
    assert event.command == "whoami"
    assert "executed:whoami" in event.output_excerpt
    assert sink.events == [event]


async def test_nonzero_exit_code_parsed_from_error_channel(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server), K8sCredential(token="sa-token"), _scope("team-a")
    )
    sink = _RecordingSink()

    event = await channel.run_command("fail-me", sink, sequence=3)

    assert event.exit_code == 7
    assert event.sequence == 3


async def test_stderr_is_merged_into_excerpt(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server), K8sCredential(token="sa-token"), _scope("team-a")
    )
    sink = _RecordingSink()

    event = await channel.run_command("emit-stderr", sink, sequence=0)

    assert "warning-on-stderr" in event.output_excerpt


async def test_run_script_emits_sequential_events(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server), K8sCredential(token="sa-token"), _scope("team-a")
    )
    sink = _RecordingSink()

    events = await channel.run_script(["a", "b", "fail-c"], sink, start_sequence=10)

    assert [e.sequence for e in events] == [10, 11, 12]
    assert [e.exit_code for e in events] == [0, 0, 7]


# --- 安全回归：凭据经 header、不经 URL/命令行 -------------------------------------


async def test_token_delivered_via_header_not_url(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server), K8sCredential(token="super-secret-token"), _scope("team-a")
    )
    sink = _RecordingSink()

    await channel.run_command("id", sink, sequence=0)

    captured = k8s_server.captured[-1]
    assert captured.authorization == "Bearer super-secret-token"
    # 凭据绝不出现在 exec URL 的 path/query 中。
    assert "super-secret-token" not in captured.path
    assert captured.subprotocol == V4_CHANNEL_SUBPROTOCOL


def test_credential_rejects_empty_token() -> None:
    with pytest.raises(K8sChannelError) as excinfo:
        K8sCredential(token="")
    assert excinfo.value.code == "K8S_CREDENTIAL_MISSING"


def test_credential_repr_masks_token() -> None:
    assert "secret" not in repr(K8sCredential(token="secret"))


# --- 安全回归：namespace 作用域强制 ---------------------------------------------


async def test_namespace_outside_scope_is_rejected_before_connect(
    k8s_server: _RunningServer,
) -> None:
    with pytest.raises(K8sChannelError) as excinfo:
        await K8sExecChannel.open(
            _target(k8s_server, namespace="kube-system"),
            K8sCredential(token="sa-token"),
            _scope("team-a", "team-b"),
        )
    assert excinfo.value.code == "K8S_NAMESPACE_FORBIDDEN"
    # 越权在建连前即阻断：服务端不应收到任何请求。
    assert k8s_server.captured == []


async def test_namespace_within_scope_is_allowed(k8s_server: _RunningServer) -> None:
    channel = await K8sExecChannel.open(
        _target(k8s_server, namespace="team-b"),
        K8sCredential(token="sa-token"),
        _scope("team-a", "team-b"),
    )
    sink = _RecordingSink()

    event = await channel.run_command("hostname", sink, sequence=0)

    assert event.exit_code == 0


# --- 安全回归：TLS 强校验（拒绝 TOFU、拒绝不可信证书、拒绝明文） --------------------


async def test_missing_ca_is_rejected(k8s_server: _RunningServer) -> None:
    with pytest.raises(K8sChannelError) as excinfo:
        await K8sExecChannel.open(
            _target(k8s_server, ca=None),
            K8sCredential(token="sa-token"),
            _scope("team-a"),
        )
    assert excinfo.value.code == "K8S_TLS_CA_MISSING"


async def test_http_endpoint_is_rejected() -> None:
    target = K8sTarget(
        api_server="http://127.0.0.1:6443",
        namespace="team-a",
        pod="web-0",
        server_ca="dummy",
    )
    with pytest.raises(K8sChannelError) as excinfo:
        await K8sExecChannel.open(target, K8sCredential(token="sa-token"), _scope("team-a"))
    assert excinfo.value.code == "K8S_INSECURE_TRANSPORT"


async def test_untrusted_server_cert_is_rejected(k8s_server: _RunningServer) -> None:
    # 用另一张与服务端无关的自签证书作为 CA：TLS 校验必须失败，绝不 trust on first use。
    other_ca, _ = _self_signed_cert()
    channel = await K8sExecChannel.open(
        _target(k8s_server, ca=other_ca),
        K8sCredential(token="sa-token"),
        _scope("team-a"),
    )
    sink = _RecordingSink()

    with pytest.raises(K8sChannelError) as excinfo:
        await channel.run_command("id", sink, sequence=0)
    assert excinfo.value.code == "K8S_TLS_HANDSHAKE_FAILED"
    assert sink.events == []


async def test_invalid_ca_material_is_rejected(k8s_server: _RunningServer) -> None:
    with pytest.raises(K8sChannelError) as excinfo:
        await K8sExecChannel.open(
            _target(k8s_server, ca="-----BEGIN CERTIFICATE-----\nnot-base64\n-----END CERTIFICATE-----"),
            K8sCredential(token="sa-token"),
            _scope("team-a"),
        )
    assert excinfo.value.code == "K8S_TLS_CA_INVALID"


# --- 单元：退出状态解析 ---------------------------------------------------------


def test_parse_exit_status_success() -> None:
    assert _parse_exit_status(json.dumps({"status": "Success"}).encode()) == 0


def test_parse_exit_status_nonzero() -> None:
    payload = json.dumps(
        {"status": "Failure", "details": {"causes": [{"reason": "ExitCode", "message": "42"}]}}
    ).encode()
    assert _parse_exit_status(payload) == 42


def test_parse_exit_status_unknown_returns_none() -> None:
    assert _parse_exit_status(None) is None
    assert _parse_exit_status(b"") is None
    assert _parse_exit_status(b"not-json") is None
    assert _parse_exit_status(json.dumps({"status": "Failure"}).encode()) is None
