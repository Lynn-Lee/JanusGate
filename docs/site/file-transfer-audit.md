# 文件传输审计（#t78 FTPLog 首切片）

`#t78` 首切片落地 JumpServer `FTPLog` 等价的文件传输分类日志：SFTP 每次上传/下载写入 `file_transfer_logs`，并**先**追加一条 `#t61` hash chain 审计事件，再以 `audit_event_id` 回指。文件正文不落库。

## 入库端点

- `POST /api/v1/session-recordings/{recording_id}/file-transfers`（`session-recordings:write`）
- `POST /api/v1/connectors/{connector_id}/session-recordings/{recording_id}/file-transfers`（`connectors:write`）
- `GET /api/v1/session-recordings/{recording_id}/file-transfers`
- `GET /api/v1/file-transfers/`（当前租户列表；`session-recordings:read` 或 `audit:read`）

连接器侧使用 `HttpFileTransferEventSink` 对接 `#t69` 已有的 `FileTransferEventSink` 协议，无需改 SFTP 通道。

## 安全语义

- 跨租户、不存在或已关闭录制统一 `404 SESSION_RECORDING_NOT_FOUND`。
- inactive / 跨租户 connector：`403 CONNECTOR_NOT_ACTIVE` / `404 CONNECTOR_NOT_FOUND`。
- `remote_path` 脱敏 `token=` / `password=` / `secret=` / `credential=` 赋值片段。
- 成功传输必须带 64 位 hex SHA-256；失败允许空摘要。非法摘要 `400 FILE_TRANSFER_SHA256_INVALID`。
- 审计 metadata 只含路径、方向、字节数、摘要、状态与错误码，不落文件内容。

## 本切片未覆盖

作业日志、会话共享与监控联机、端点路由、命令/录像多存储后端（含 ES）。操作/改密/活动/在线会话日志见 [`classified-audit.md`](classified-audit.md)。
