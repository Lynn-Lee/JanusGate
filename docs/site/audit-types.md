# 审计类型补全（#t78）

Phase 6 #t78 把 JumpServer 的分类审计接到 JanusGate 已有的 **#t61 append-only hash chain + WORM** 上。分类日志**不另建可绕过账本的表**：`OperateLog` / `ActivityLog` / `FTPLog` / `PasswordChangeLog` / `JobLog` 都是 `audit_events.category` 过滤视图。

本切片明确延后：会话共享与监控联机、连接端点路由、命令/录像多存储后端（S3/OSS/ES）。

## 类型与 API

| JumpServer | JanusGate category | 写入 | 列表 |
| --- | --- | --- | --- |
| OperateLog | `operate` | `POST /api/v1/audits/operate-logs` | `GET /api/v1/audits/operate-logs` |
| ActivityLog | `activity`（列表同时包含 `auth` / `session`） | `POST /api/v1/audits/activity-logs` | `GET /api/v1/audits/activity-logs` |
| FTPLog | `file_transfer` | 连接器 `POST /api/v1/connectors/{connector_id}/file-transfers` | `GET /api/v1/audits/file-transfers` |
| PasswordChangeLog | `password_change` | 登录改密、账号凭据轮换成功/失败 | `GET /api/v1/audits/password-changes` |
| JobLog | `job` | Ansible playbook 终态 `completed` / `failed` | `GET /api/v1/audits/job-logs` |
| UserSession | 在线 PAM 会话 | 读 `sessions` 未关闭状态 | `GET /api/v1/audits/online-sessions` |

通用列表 `GET /api/v1/audits/events` 增加可选 `category` 过滤。所有分类写入仍走 `AuditService.create_event`：租户序号、`previous_event_hash`、SHA-256 `event_hash`、SIEM 与合规报表/WORM 不变。

## 文件传输

#t69 SFTP 通道继续产出 `FileTransferEvent`（路径、方向、字节数、sha256、成功/失败）。#t78 接线：

- HTTP：`HttpFileTransferEventSink` → `POST /api/v1/connectors/{id}/file-transfers`
- 进程内生产调度器：`AuditServiceFileTransferSink`（按会话绑定身份，直接写账本）

跨租户或非 active 连接器返回 `CONNECTOR_NOT_FOUND`（404，避免探测）。失败传输同样落账。响应不含文件正文、token、凭据。

## 安全边界

- metadata 继续走 `redact_metadata`（password / token / secret / credential 等）
- 改密日志不记录旧/新密码或 Vault `secret_id`
- 作业日志丢弃 `stdout` / `stderr` / `inventory` / `output`
- 在线会话不返回 `connection_url`、`connection_token_id`
- 读接口要 `audit:read`（或 `admin`）；连接器入库要 `connectors:write`（或 `admin`）
- 查询强制当前用户 `tenant_id`，不接受前端传入 tenant

## 控制台

`/audits` 增加标签页：全部事件、操作日志、活动日志、文件传输、改密日志、作业日志、在线会话。详情抽屉仍只展示脱敏 metadata。
