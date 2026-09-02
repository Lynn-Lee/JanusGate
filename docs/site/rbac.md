# RBAC 角色与权限（#t63）

`#t63` 提供统一 RBAC 模型，替代硬编码的 `admin` / `workflow:admin` 字符串判断。角色、绑定与对象级权限均按租户隔离，查询强制走 `scoped_select()`。

## 数据模型

- `roles`：角色定义，含 `scope_type`（`system` / `organization`）、全局权限 JSON 与菜单权限 JSON。
- `role_bindings`：将角色绑定到 `user` 或 `user_group` 主体，可带组织 scope。
- `role_object_permissions`：对象级权限，限定角色对特定 `organization` / `team` / `project` 资源的动作。

每个租户首次访问时自动种子四个内置角色：

| builtin_key | 名称 | 说明 |
|-------------|------|------|
| `system_admin` | 系统管理员 | 全部管理权限，含 `admin` 与 `rbac:manage` |
| `org_admin` | 组织管理员 | 组织范围内资产、账号、工单管理 |
| `auditor` | 审计员 | 审计只读 |
| `user` | 普通用户 | 默认 `assets:read` + `sessions:connect` |

内置角色不可修改或删除。

## 权限解析

`RbacResolver.resolve()` 合并用户与用户组绑定的角色权限：

- `system` scope 绑定全局生效。
- `organization` scope 绑定仅在用户组织上下文匹配时生效。
- 对象级权限按资源类型与当前用户的 org/team/project 上下文过滤。
- 无绑定时回退到普通用户默认权限。
- `is_superuser` 用户登录时自动绑定 `system_admin` 角色，承接历史超级用户迁移路径。

登录与 refresh token 签发时调用解析器，将 `permissions`、`menu_permissions` 与 `role_ids` 写入 JWT。

## API

所有路径前缀 `/api/v1`：

- `GET /rbac/effective`：当前用户有效权限（任意登录用户可读）。
- `GET/POST/PATCH/DELETE /roles/`：角色管理（`rbac:read` / `rbac:manage`）。
- `GET/POST/DELETE /role-bindings/`：角色绑定管理。
- `POST /roles/{role_id}/object-permissions`：为自定义角色添加对象级权限。

跨租户访问统一返回 404。`admin` 权限仍可作为全局通配符，与现有路由守卫兼容。

## 与资产授权的关系

`#t64` 的 `AssetPermission` 负责会话级资产连接授权；`#t63` RBAC 负责平台管理面与菜单可见性。二者独立判定，admin 不绕过资产授权。

## 验证

- 单元测试覆盖内置角色种子、组织 scope、用户组绑定、对象级权限解析。
- API 测试覆盖 CRUD、内置角色不可变、跨租户 404。
- 登录集成测试验证 JWT 中 RBAC 权限字段。
