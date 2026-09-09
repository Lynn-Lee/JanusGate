# 操作日志与改密日志（#t78 第二切片）

本切片落地 JumpServer `OperateLog` / `PasswordChangeLog` 等价的分类审计：管理面写操作与成功改密先追加 `#t61` hash chain 事件，再以 `audit_event_id` 回指分类表。摘要与 metadata **不保存密码、secret、token 或凭据正文**。

## 列表端点

- `GET /api/v1/operate-logs/`（`audit:read` 或 `admin`）
- `GET /api/v1/password-change-logs/`（`audit:read` 或 `admin`）

跨租户列表为空，不暴露对端资源是否存在。缺少读权限 `403`。

## 写入接线

- 资产创建 / 更新 / 删除、账号创建 / 更新写入 `operate_logs`（`admin.operate`）。
- `POST /api/v1/auth/password/change` 成功后写入 `password_change_logs`（`auth.password_change`，severity=medium）。

## 本切片未覆盖

活动日志、在线会话、作业日志、会话共享与监控联机、端点路由、命令/录像多存储后端（含 ES）。
