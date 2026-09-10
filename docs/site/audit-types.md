# 审计类型与会话高级能力

#t78 把操作 / 活动 / 文件传输 / 改密 / 在线会话 / 作业日志并入 #t61 hash chain 与 WORM 归档，并补齐会话共享、连接端点路由、命令与录像多后端存储的第一闭环。

## 分类审计

- `POST /api/v1/audits/typed`：`log_kind` 为 `operate` / `activity` / `ftp` / `password` / `online-sessions` / `job` / `session-shares`。
- `GET /api/v1/audits/typed/{log_kind}`：按固定 `event_type` 过滤。
- `POST /api/v1/audits/ftp-logs`：SFTP 文件传输日志入库；生产 SFTP 通道在未注入测试 sink 时走 `HashChainFileTransferSink`。
- 敏感 metadata 继续脱敏；序号与 `previous_event_hash` 连续。

## 会话共享与监控联机

- `POST /api/v1/sessions/{id}/shares`：仅会话 owner 可邀请 guest，`mode=watch|join`。
- `POST /api/v1/sessions/{id}/shares/{share_id}/join`：仅 guest 可加入。
- 共享与加入均写入 hash chain（`session.share` / `session.join`）。

## 端点路由

- `POST /api/v1/session-ops/endpoints` 与 `/endpoint-rules`
- `GET /api/v1/session-ops/endpoint-rules/resolve?protocol=&host=`：按协议与主机后缀、priority 选端点。

## 命令 / 录像存储

- 后端 kind：`local` / `s3` / `oss` / `es`；purpose：`command` / `replay`。
- 配置禁止 secret/access_key 等凭据键；列表回显脱敏。
- 本切片 s3/oss/es 以本地隔离前缀适配，ES 命令检索为目录内 JSON 全文包含匹配。
