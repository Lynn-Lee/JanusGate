# 审计类型与会话高级能力

#t78 把 JumpServer 对标的分类日志接到已有的 #t61 append-only hash chain 与 WORM 归档，而不是另建可改写的平行表。会话共享、端点路由和存储后端是同一切片的运营配置，明文凭据与分享码哈希分离。

## 分类日志

| 类型 | `kind` | 典型 `event_type` |
|------|--------|-------------------|
| 操作日志 | `operate` | `operate.update` |
| 活动日志 | `activity` | `activity.login` |
| 文件传输 | `file_transfer` | `file_transfer.upload` |
| 改密日志 | `password_change` | `password_change.updated` |
| 在线会话 | `user_session` | `user_session.shared` |
| 作业日志 | `job` | `job.succeeded` |
| 集成应用 | `integration` | `integration.sync` |

- `POST /api/v1/audits/typed` 与 `GET /api/v1/audits/typed?kind=`
- `POST /api/v1/audits/ftp-logs`：SFTP 传输入库，失败传输同样落链
- `POST /api/v1/audits/job-logs`：自动化作业结果
- `POST /api/v1/auth/password/change` 成功后写改密日志，不记录新旧密码
- `GET /api/v1/audits/online-sessions`：当前租户 `active` 会话快照（可变状态）

生产 SFTP 默认 `HashChainFileTransferSink`，在打开通道时绑定会话身份后写入 hash chain。

## 会话共享与监控联机

- `POST /api/v1/session-ops/sessions/{id}/shares` 生成只读共享码，库中只存 SHA-256
- `POST /api/v1/session-ops/joins` 以 `observe` 加入，不返回凭据或 connection token
- 过期、吊销、错误码统一 `SESSION_SHARE_INVALID`，避免枚举有效邀请

## 连接端点与存储后端

- 端点只登记 `host:port`，规则按优先级 + 协议 + 可选资产匹配
- 存储 `kind=command|replay`，`provider=local|s3|oss|es`
- 配置 JSON 拒绝 `password` / `token` / `secret` / `access_key`
- 本切片 s3/oss/es 使用本地适配前缀做命令检索；云 SDK 直连与录像转码属 #t70

控制台：审计页分类筛选；会话页「共享监控」与端点列表。
