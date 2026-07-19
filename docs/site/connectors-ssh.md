# SSH 连接器预研通道

#t69 预研切片在 Connector 进程内实现了真实的 SSH 执行通道（`backend/app/connectors/ssh_channel.py`），用于验证 Phase 6 最高技术风险点：能否在强安全约束下建立单协议端到端连接。通道基于纯 Python 的 `asyncssh`，不 fork 任何 `ssh` / `sshpass` 子进程；命令执行结果统一流经命令事件管线入库。

## 安全约束

以下约束逐条对应 §3.6.3 的历史问题，均由 `backend/tests/connectors/test_ssh_channel.py` 证明关闭。

- **P0#7 弱算法**：仅协商 `MODERN_KEX_ALGS` / `MODERN_ENCRYPTION_ALGS` / `MODERN_MAC_ALGS` / `MODERN_HOST_KEY_ALGS` 白名单内的现代算法。服务端只提供 SHA-1 MAC、CBC 等弱算法时，协商直接失败并抛出 `SSH_ALGORITHM_NEGOTIATION_FAILED`。
- **P0#15 私钥落盘**：私钥仅以内存字节/字符串经 `asyncssh.import_private_key` 加载，全程不写临时文件、不引用磁盘路径。`SshCredential` 的私钥与密码字段在 `repr` 中屏蔽，避免日志、异常回溯与审计意外泄露。
- **P0#16 凭据经命令行**：凭据作为库调用参数传入，无子进程、无命令行参数。同时显式关闭 SSH agent（`agent_path=None`）、默认密钥扫描与用户 `ssh_config`（`config=None`），杜绝磁盘凭据来源。
- **P0#17 AutoAddPolicy**：`known_hosts` 由调用方提供的可信主机公钥严格构造。未知或不匹配的主机密钥一律拒绝连接（`SSH_HOST_KEY_REJECTED`），绝不 trust on first use。

## 算法白名单要点

白名单在模块常量中集中声明，只保留现代套件：

- **密钥交换**：curve25519 / curve448、NIST ECDH、`diffie-hellman-group14-sha256` 及以上，以及后量子混合 `mlkem768x25519-sha256`；排除全部 SHA-1、`group1`、`gss-*` 与 `*.ssh.com` 遗留项。
- **对称加密**：仅 AEAD 与 CTR（`chacha20-poly1305`、`aes*-gcm`、`aes*-ctr`）；排除 CBC、arcfour、3des、blowfish、seed。
- **MAC**：仅 SHA-2（优先 ETM 变体）；排除 hmac-md5、hmac-sha1 及全部变体。
- **主机密钥**：ed25519 / ed448、NIST ECDSA、`rsa-sha2-256/512`；排除 `ssh-rsa`(SHA-1) 与 `ssh-dss`。

## 命令事件入库端点

通道每执行一条命令即产生一条 `CommandEvent`（对齐 #t46 命令事件管线契约），字段与入库端点的 `SessionCommandEventCreate` 一致：`sequence`（会话内单调递增序号）、`command`（命令原文）、`exit_code`（退出码，无法获取为 `null`）、`output_excerpt`（截断至 4096 字符的输出摘要）。

事件通过以下端点入库：

```text
POST /api/v1/connectors/{connector_id}/session-recordings/{recording_id}/commands
```

请求体示例：

```json
{
  "sequence": 0,
  "command": "id",
  "exit_code": 0,
  "output_excerpt": "uid=0(root) gid=0(root)"
}
```

`SshChannel.run_command` / `run_script` 通过注入的 `CommandEventSink` 投递事件，由 Sink 实现负责将事件 POST 到该端点；Sink 实现需保证幂等或按 `sequence` 去重。

## 交互式 PTY 通道与命令流解析

除逐条 `exec` 执行外，`backend/app/connectors/ssh_interactive.py` 在同一安全连接（`SshChannel.open`，安全约束不放宽）之上提供交互式 PTY shell，并把操作员在交互会话中执行的每条命令解析、投递到命令事件管线。

- **命令流解析（`InteractiveCommandParser`）**：从操作员的**键盘输入流**（而非终端回显）重建实际敲下的命令。堡垒机审计关注「用户到底执行了什么」，故以输入为准，并正确处理退格、`Ctrl-U` 清行、`Ctrl-C` 取消当前行（该行不作为命令产出）与 ANSI CSI 转义序列（方向键 / 颜色码等）的丢弃。
- **输出归属（`SshInteractiveSession`）**：采用 **prompt 跟踪**切分每条命令的输出——`open` 消费到首个 prompt 后就绪，`run_command` 发送命令并读取到下一个 prompt 之间的内容作为该命令输出，并剥离 PTY 回显的命令行本身。真实部署应将目标 shell 的 `PS1` 设为一个唯一 prompt 标记，以便精确切分。

交互会话产出的 `CommandEvent` 与 `exec` 路径同构（`exit_code` 为 `null`，交互 shell 不逐条回传退出码），复用同一命令事件入库端点。已知限制：暂不解释 Tab 补全与光标中间插入/移动，留待完整 #t69 细化。

## SFTP 文件传输与传输审计

`backend/app/connectors/ssh_sftp.py` 在同一安全连接之上提供 SFTP 传输（`SftpChannel`，安全约束不放宽），每次上传/下载产出一条 `FileTransferEvent` 传输审计事件投递到下游 sink。

- **审计字段**：`remote_path`、`direction`（`upload`/`download`）、`size_bytes`、`sha256`、`status`（`success`/`failed`）、`error_code`。携带 SHA-256 摘要与字节数，便于并入 #t61 的 hash chain 与 WORM 归档（#t78 约束）。
- **失败可见**：传输失败时先投递一条 `status=failed` 的事件再抛出类型化错误（`SSH_SFTP_UPLOAD_FAILED` / `SSH_SFTP_DOWNLOAD_FAILED`），保证失败传输在审计中不丢失。
- **落库**：#t78 的文件传输日志入库端点尚未建立（属 M6），本切片先以 `FileTransferEventSink` 协议解耦下游；待 #t78 端点落地后提供 HTTP sink 即可接线，无需改动传输通道（与命令事件 sink 一致）。

## 主机密钥采集（scan → 审批 → 固定）

严格主机密钥校验（P0#17）要求 `SshTarget` 预置可信主机公钥，而这个 key 需要一条安全的获取途径。`backend/app/connectors/ssh_hostkey.py` 提供堡垒机常见的「采集主机密钥」步骤：

- **`scan_host_key(host, port)`**：仅完成密钥交换取回目标主机公钥即断开，**不做用户认证、不信任该 key**。返回 `HostKeyScan`（`key_type`、`public_key` 单行、`SHA256:` 指纹）。
- **闭环**：管理员带外核对 `fingerprint` 并审批后，`public_key` 即可作为 `SshTarget.trusted_host_key` 固定，启用严格校验。
- **采集期强制现代算法**：只提供弱算法（如 SHA-1 KEX）的服务器在协商阶段被拒绝（`SSH_HOST_KEY_SCAN_FAILED`），不会被采集固定，与通道的 P0#7 约束一致。
- **SSRF 注意**：`scan_host_key` 会向传入 `host` 发起连接，调用方须先用资产白名单 / SSRF 防护校验 `host`，不得透传不可信输入。

## 接线到会话网关（进程内）

`backend/app/connectors/session_runtime.py` 把上述连接器能力接到会话网关 `SessionGatewayService` 的 `CONNECTING → ACTIVE` 环节。

- **进程边界**：`ConnectorScheduler` 这个协议就是连接器进程边界——生产环境 `dispatch` 是发往远端连接器的 RPC。`ConnectorRuntimeScheduler` 是它的**进程内实现**（dev / 单机 / 测试）：在同进程内解析目标 + 凭据并打开真实通道。要换远端形态，另写一个实现 `ConnectorScheduler` 的传输类即可，网关无需改动。
- **凭据边界**：网关只向 `dispatch` 传身份（`ConnectorDispatchRequest`：tenant/subject/asset/account/protocol），**不持有凭据**；凭据仅在连接器侧经 `SessionConnectionResolver` 解析后出现。
- **生命周期**：`ConnectorSessionRuntime` 按 `ConnectorSessionMode`（`exec`/`interactive`/`sftp`）打开对应通道并登记；网关会话 `close`/`revoke` 时通过 `release` 释放连接器会话（释放失败降级为审计事件，不阻断关闭，另有心跳租约兜底回收）。
- **默认仍为 Noop**：路由默认使用 `NoopConnectorScheduler`，本运行时的生产接线需要把资产注册表 + 凭据保险库桥接进来的 `SessionConnectionResolver` 实现（待落地）；机制与端到端测试已就绪（`tests/connectors/test_session_runtime.py`，含完整 `create_session → ACTIVE → close` 集成用例）。
