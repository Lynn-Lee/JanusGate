# RBAC 角色与权限体系（#t63）

`#t63` 提供单一角色模型的 RBAC 基础：内置角色 + 自定义角色 + 角色绑定，并把历史上由 `is_superuser` 直接映射的 `admin` / `assets:read` 字符串权限收敛为可解释、可扩展的角色授予。

## 设计原则

- **单一角色模型**：内置角色在代码中声明，不按 edition 分支（对应关闭历史问题 P1#11 xpack 侵入）。
- **强制租户过滤**：角色与角色绑定的所有查询都经过 `app.tenancy.scope.scoped_select`（对应关闭 P2#6 Root 组织无过滤）。
- **字段默认 NOT NULL**：模型字段默认非空（对应关闭 P2#10 `null=True` 反模式）。
- **平滑迁移**：显式角色绑定只会在 `is_superuser` 回退基线之上**新增**权限，不会移除既有账号历史权限。

## 数据模型

- `roles`：租户内的**自定义**角色（内置角色不落库），保存 `tenant_id`、`name`、`scope`（`system` / `org`）、`permissions_json` 和 `description`；`(tenant_id, name)` 唯一。
- `role_bindings`：把角色（内置 key 或自定义角色 id）授予用户；`organization_id` 空串表示 system 级绑定，非空表示 org 级绑定；`(tenant_id, user_id, role_id, organization_id)` 唯一。

## 内置角色

| key | 名称 | scope | 权限要点 |
|-----|------|-------|----------|
| `system_admin` | 系统管理员 | system | `admin` + 资产/审计/工单/RBAC/租户全量 |
| `org_admin` | 组织管理员 | org | 资产读写、工单管理、`rbac:read`，不含全局 `admin` |
| `auditor` | 审计员 | system | `audit:read`、`assets:read` |
| `user` | 普通用户 | system | `assets:read` |

## 权限解析

`RbacService.resolve_effective_permissions()` 在登录、2FA 换取会话 token 和 refresh 时被调用：

- 先取 `is_superuser` 回退基线（超管=系统管理员权限集，普通用户=`assets:read`），保证无绑定账号行为与历史完全一致。
- 再并入该用户在当前租户下所有 `RoleBinding` 对应角色的权限（内置角色读常量，自定义角色读 `permissions_json`）。
- 跨租户绑定不生效；权限并集去重后写入 JWT，由 `require_permission` 与各路由消费。

## API

所有路径带 `/api/v1` 前缀。读接口要求 `admin` 或 `rbac:read`；写接口要求 `admin` 或 `rbac:admin`。

- `GET /rbac/roles`：列出内置角色 + 当前租户自定义角色。
- `POST /rbac/roles`：创建自定义角色（角色 id 由后端生成，名称不得与内置角色重名）。
- `GET /rbac/role-bindings`：列出当前租户角色绑定，可按 `user_id` 过滤。
- `POST /rbac/role-bindings`：把角色授予用户；`role_id` 可为内置 key 或自定义角色 id，未知角色返回 `ROLE_NOT_FOUND`，org 级绑定必须提供存在的 `organization_id`。
- `DELETE /rbac/role-bindings/{binding_id}`：删除绑定；跨租户访问返回 404。

## 验证与边界

- 后端回归覆盖：无绑定回退基线（超管/普通用户）、内置角色并集、自定义角色并集、跨租户绑定隔离，以及管理 API 的权限门禁、租户隔离、未知角色与 org 绑定校验。
- 本切片是 RBAC 基础：用户组（`user_group`）主体、菜单权限精细化与角色绑定生效的组织切换 UI 属于后续切片；用户组授权接入点已由 #t64 的策略请求上下文 `group_ids` 预留。
