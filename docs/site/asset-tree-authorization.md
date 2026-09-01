# 资产树与 AssetPermission（#t64）

`#t64` 提供资产树组织和资产级授权。管理面维护节点、挂载资产和授权；使用面只展示当前用户实际拥有有效 `connect` 授权的资产。

## 数据模型

- `asset_nodes`：租户内唯一根节点；节点保存 `parent_id` 和 `ancestor_ids_json` 祖先链。
- `assets.node_id`：资产挂载到非根节点；为空表示未分组，未分组资产不继承节点授权。
- `asset_permissions`：保存 `tenant_id`、主体类型（`user` / `user_group`）、主体 ID、资源类型（`asset` / `node`）、账号、协议、动作、到期时间和 `from_ticket` 来源工单。
- 根节点只作容器，不能挂资产、授权或删除。

## 授权判定

`PolicyDecisionService.evaluate()` 是唯一会话授权入口。`connect`、`session.connect` 和 `asset.connect` 请求只读取当前租户经 `scoped_select()` 加载的 `AssetPermission`：

- 用户主体匹配当前用户 ID；用户组主体匹配请求上下文 `group_ids`。
- 资产直接授权优先；节点授权沿资产节点的祖先链继承，根节点授权无效。
- `account_id` 和 `protocol` 为空或 `*` 表示选择器，不收窄匹配范围；到期授权不匹配。
- 无命中、租户不一致或策略加载失败均拒绝；admin 不绕过资产授权。
- 允许结果在 `explain_trace` 中记录 permission ID 和 direct/inherited 路径。

## API

所有路径都带 `/api/v1` 前缀，并要求当前租户的资产读写权限：

- `GET/POST /asset-nodes/`：列出或创建节点。
- `PATCH/DELETE /asset-nodes/{node_id}`：重命名或删除节点；删除前必须清空子节点和资产。
- `POST /asset-nodes/{node_id}/assets`、`POST /asset-nodes/ungroup`：挂载或移出资产；配套 `*-impact` GET 路径返回可能失去连接的主体。
- `GET/POST /asset-nodes/{node_id}/permissions`：查看或创建节点授权。
- `GET/POST /asset-permissions/by-asset/{asset_id}`：查看或创建资产直接授权；列表包含节点继承授权。
- `DELETE /asset-permissions/{permission_id}`：删除直接授权。

管理 API 跨租户访问统一返回 404，不返回“没有权限”文案。创建授权时 `subject_type`、`action`、`account_id`、`protocol`、`expires_at` 和 `from_ticket` 由 API 契约校验；用户组成员关系由身份层通过策略请求上下文的 `group_ids` 提供。

## 验证与边界

- 后端回归覆盖根节点保护、树移动、祖先继承、未分组资产、过期授权、用户组授权、跨租户隔离和会话创建。
- 所有节点、资产挂载和授权加载查询经过 `app.tenancy.scope.scoped_select`，不允许直接无租户过滤读取。
- 用户组 CRUD 与成员管理由 #t63 RBAC API（`/api/v1/rbac/user-groups`）提供；本模块只消费 JWT / 策略上下文中的 `group_ids`。
