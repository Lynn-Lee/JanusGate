# K8s exec 连接器通道

#t72 在 Connector 进程内实现了真实的 Kubernetes `exec` 通道（`backend/app/connectors/k8s_exec.py`），把「在 Pod 中执行一条命令」映射为一条对齐 #t46 命令事件管线的 `CommandEvent`，与 SSH 通道复用同一条命令审计管线。通道走 WebSocket 上的 `v4.channel.k8s.io` 子协议（stdin/stdout/stderr/error 单字节通道多路复用），基于纯 Python 的 `websockets`，不 fork `kubectl` 子进程。

## 语义要点

- **每条命令一次连接**：K8s streaming exec 的语义是「一次 `exec` 请求执行一条命令」，故 `K8sExecChannel.run_command` 每次打开一个独立的 WebSocket 连接；`run_script` 按序对多条命令各建一次连接。`close` 无长连接可释放，仅为对齐通道语义与上下文管理器用法。
- **命令包裹**：操作员命令默认经 `/bin/sh -c <command>` 包裹为 argv（可通过 `exec_shell` 覆盖）。命令事件记录的 `command` 是**操作员命令原文**，而非包裹后的 argv。
- **退出码解析**：进程结束时 API Server 在 error 通道（channel 3）回传一个 `metav1.Status` 对象。`status == "Success"` 记为退出码 `0`；非零退出时从 `details.causes` 中 `reason == "ExitCode"` 的 cause 解析退出码。无法解析（无状态帧 / 非 JSON / 失败但未带退出码）时 `exit_code` 为 `null`，与 `CommandEvent` 契约一致。

## 安全约束

以下约束逐条对应 roadmap #t72「namespace 作用域强制生效」与 §3.6.3 的历史问题，均由 `backend/tests/connectors/test_k8s_exec.py` 证明关闭。

- **namespace 作用域强制**：通道以 `NamespaceScope`（授权 namespace 集合）授权。目标 namespace 不在集合内一律拒绝（`K8S_NAMESPACE_FORBIDDEN`），且在**建连之前**即阻断——即使调用方透传了越权的 `K8sTarget`，也绝不会向该 namespace 发起 exec。作用域应来自授权模型（资产授权 / #t68 namespace 作用域），不得由不可信输入直接构造。
- **TLS 强校验**（对标 SSH 的 P0#17 主机密钥强校验）：API Server 证书由调用方预置的 CA（`server_ca`，PEM）严格校验且校验主机名（`check_hostname` + `CERT_REQUIRED`）。未提供 CA 一律拒绝（`K8S_TLS_CA_MISSING`），绝不 trust on first use，也不暴露任何关闭校验的开关；不可信证书在握手阶段失败（`K8S_TLS_HANDSHAKE_FAILED`）。`api_server` 必须为 `https://`，明文 `http://` 被拒绝（`K8S_INSECURE_TRANSPORT`）。
- **凭据仅内存、不经命令行/URL**（对标 P0#15 / P0#16）：Bearer token 仅在内存持有、经 `Authorization` 请求头发送，绝不进入 URL query 或任何命令行；`K8sCredential` 的 token 字段在 `repr` 中屏蔽，避免日志、异常回溯与审计意外泄露。

## 命令事件入库端点

通道每执行一条命令即产生一条 `CommandEvent`（对齐 #t46 命令事件管线契约），字段与 SSH 通道同构：`sequence`（会话内单调递增序号）、`command`（命令原文）、`exit_code`（退出码，无法获取为 `null`）、`output_excerpt`（合并 stdout/stderr 并截断至 4096 字符的输出摘要，stderr 保留独立预算以保住失败取证）。

事件通过与 SSH 通道一致的端点入库，由注入的 `CommandEventSink` 投递：

```text
POST /api/v1/connectors/{connector_id}/session-recordings/{recording_id}/commands
```

## 目标与凭据

- **`K8sTarget`**：`api_server`（`https://host:port`）、`namespace`、`pod`、`container`（可选，`None` 时由 API Server 选默认容器）、`server_ca`（严格校验用的 CA PEM）。
- **`K8sCredential`**：`token`（ServiceAccount / OIDC Bearer token，内存字符串，`repr` 屏蔽）。
- **`NamespaceScope`**：`namespaces`（被授权的 namespace 名称集合）。

## 已知边界

- 本切片聚焦一次性命令 exec + 命令审计 + namespace 作用域 + TLS 强校验，暂不实现交互式 PTY（`stdin=true` + `tty=true` + resize 通道）与 `attach`；后续可在同一 `v4.channel.k8s.io` 帧协议上扩展。
- 短期 token 签发（K8s TokenRequest API，对应 #t68 安全增强项）由凭据保险库侧负责，本通道只消费传入的 token，不负责其签发与轮换。
- 与会话网关的接线（`session_runtime.py` 的 `ConnectorSessionMode`）沿用 SSH 通道已建立的 `SessionConnectionResolver` + `ConnectorScheduler` 边界。**#t69 生产 resolver 已 QA SHIP**，但仅覆盖 SSH（exec / interactive / sftp）；连接列表隐藏 k8s，本通道不在本次生产装配范围。
