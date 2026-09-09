# 分类审计（#t78）

本切片落地 JumpServer `OperateLog` / `PasswordChangeLog` / `ActivityLog` / `UserSession` 等价的分类审计。管理面写操作、成功改密、交互式登录与结束在线会话均先追加 `#t61` hash chain 事件，再以 `audit_event_id`（结束会话另有 `ended_audit_event_id`）回指分类表。摘要、detail 与 metadata **不保存密码、secret、token 或凭据正文**。

## 列表端点

- `GET /api/v1/operate-logs/`（`audit:read` 或 `admin`）
- `GET /api/v1/password-change-logs/`（`audit:read` 或 `admin`）
- `GET /api/v1/activity-logs/`（`audit:read` 或 `admin`）
- `GET /api/v1/online-sessions/`（`audit:read` 或 `admin`）

跨租户列表为空，不暴露对端资源是否存在。缺少读权限 `403`。

## 结束在线会话

- `POST /api/v1/online-sessions/{session_id}/end`（仅 `admin`）

跨租户或未知 ID 返回 `404 ONLINE_SESSION_NOT_FOUND`。已结束会话幂等返回当前行，不再追加 hash chain。

## 写入接线

- 资产创建 / 更新 / 删除、账号创建 / 更新写入 `operate_logs`（`admin.operate`），并复用同一 `audit_event_id` 写入 `activity_logs`。
- `POST /api/v1/auth/password/change` 成功后写入 `password_change_logs`（`auth.password_change`，severity=medium），并复用同一事件写入活动时间线。
- `POST /api/v1/auth/login` 在未进入 2FA 挑战时、以及 `POST /api/v1/auth/login/2fa` 成功后写入 `online_user_sessions`（`auth.login`）与活动时间线。失败登录与仅签发 MFA challenge 不落在线会话。不保存 access/refresh token。
- 活动时间线复用已有 hash chain 事件，不为同一动作再写第二条审计事件。

## 本切片未覆盖

作业日志、会话共享与监控联机、端点路由、命令/录像多存储后端（含 ES）。
