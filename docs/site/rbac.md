# RBAC 角色权限（#t63）

JanusGate 的 RBAC 采用单一角色模型，覆盖 **system / org 双 scope**，禁止 edition 条件分支。权限判定仍通过 JWT 中的 `permissions` 数组传递给既有 `require_permission` 与 `scoped_select` 路径，**不绕过** #t64 资产授权与 #t65 ACL 判定。

## 模型

| 表 | 用途 |
| --- | --- |
| `rbac_roles` | 角色定义：权限集合、菜单集合、scope、可选 `organization_id` |
| `rbac_role_bindings` | 将角色授予 `user` 或 `user_group` |
| `rbac_user_groups` | 用户组及成员列表，供 RBAC 绑定与 #t64 资产授权 `user_group` 主体复用 |
| `rbac_object_permissions` | 对象级细粒度授权（资源类型 + 资源 ID + 动作） |

## 内置角色

每个租户首次解析权限时会幂等补全以下内置角色：

| builtin_key | 名称 | 典型权限 |
| --- | --- | --- |
| `system_admin` | 系统管理员 | 全量管理权限 + `rbac:read/write` |
| `org_admin` | 组织管理员 | 租户管理面权限 |
| `auditor` | 审计员 | `audit:read`、`workflow:audit` |
| `user` | 普通用户 | `assets:read`、`sessions:connect`、`workflow:request` |

## 权限解析规则

1. 若用户存在 **active 角色绑定**（直接 user 绑定或通过 user_group 间接绑定），合并所有绑定角色的权限与菜单。
2. 若无绑定：`is_superuser=true` 回退为系统管理员权限集；否则回退为普通用户权限集。
3. 登录与 refresh token 时通过 `RbacService.resolve_effective_rbac()` 写入 JWT 的 `permissions`、`menu_permissions`、`group_ids`。
4. `admin` 权限仍作为 `scoped_select` 的租户过滤放宽条件；**不**作为 #t64 资产 connect 判定绕过条件。

## 管理 API

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/api/v1/rbac/roles` | `rbac:read` 或 `admin` |
| POST | `/api/v1/rbac/roles` | `rbac:write` 或 `admin` |
| GET | `/api/v1/rbac/role-bindings` | `rbac:read` 或 `admin` |
| POST | `/api/v1/rbac/role-bindings` | `rbac:write` 或 `admin` |
| GET | `/api/v1/rbac/user-groups` | `rbac:read` 或 `admin` |
| POST | `/api/v1/rbac/user-groups` | `rbac:write` 或 `admin` |
| PATCH | `/api/v1/rbac/user-groups/{group_id}/members` | `rbac:write` 或 `admin` |
| GET | `/api/v1/rbac/object-permissions` | `rbac:read` 或 `admin` |
| POST | `/api/v1/rbac/object-permissions` | `rbac:write` 或 `admin` |
| GET | `/api/v1/rbac/me/effective` | 登录态 |

跨租户资源访问统一返回 `404`，不泄露存在性。

## 与 JumpServer 历史问题的对应

| 问题 | 关闭方式 |
| --- | --- |
| P1#11 xpack 侵入核心 | 单一角色模型，无 edition 条件分支 |
| P2#6 Root 组织无过滤 | 全部 RBAC 查询强制 `scoped_select()` 租户过滤 |
| P0#13 对象级授权越权 | 对象级权限独立表 + 绑定校验 + 测试回归 |

## 边界

- 本任务提供 RBAC / 用户组 CRUD 与 JWT 权限解析；**不**替换 #t64 资产授权判定。
- 菜单权限写入 JWT `menu_permissions`，前端导航接入可作为后续切片。
- SSO 用户权限映射仍待 #t76。
