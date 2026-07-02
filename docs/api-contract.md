# Phase 3 API 契约与错误码规范

> 面向 #t36 前端控制台、#t38 E2E smoke 和 #t41 QA 门禁。本文件记录当前后端稳定契约，后续新增 API 默认沿用。

更新时间：2026-07-03
范围：Phase 3 MVP 前端/后端联调契约，以及 Phase 4 多租户增量契约。

## 基础约定

- API 前缀：业务接口统一在 `/api/v1/*` 下暴露；健康检查保留 `/health`。
- 认证：默认使用 Bearer access token；未认证或 token 失效返回 `401`。
- 租户/权限：接口必须通过 `current_user` / `require_permission` 或业务层 actor-aware 查询控制租户与资源边界。Phase 4 起 `current_user` 会返回 `tenant_id`，以及可选的 `organization_id`、`team_id`、`project_id`；新增 DB 查询优先使用 `app.tenancy.scope.scoped_select()` 注入租户过滤。
- 时间字段：响应中业务时间优先使用 ISO 8601 字符串或 OpenAPI `date-time` schema，不返回本地化展示文案。
- 分页/列表：现阶段列表响应优先返回 `items + total`；资产等历史接口仍保持数组响应，前端需按 OpenAPI 读取。

## 统一错误响应

所有由 FastAPI `HTTPException`、业务 `AppError`、`ValueError` 与请求体验证触发的错误，统一返回 `ErrorResponse`：

```json
{
  "code": "WORKFLOW_REQUEST_NOT_FOUND",
  "message": "WORKFLOW_REQUEST_NOT_FOUND",
  "detail": "WORKFLOW_REQUEST_NOT_FOUND",
  "request_id": ""
}
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `code` | 前端和 QA 可稳定断言的错误码。若 `detail` 是大写业务码（如 `SELF_APPROVAL_NOT_ALLOWED`），直接提升为 `code`；否则按 HTTP 状态映射。 |
| `message` | 可展示或可记录的错误摘要。中文业务错误会原样保留。 |
| `detail` | 兼容 FastAPI 旧契约的详情字段；已有前端可继续读取。422 校验错误为字段错误数组。 |
| `request_id` | 请求追踪 ID；当前未接入网关时为空字符串，若上游传 `X-Request-ID` 会回显。 |

默认状态码映射：

| HTTP | 默认 `code` |
| --- | --- |
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHORIZED` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND` |
| 422 | `VALIDATION_ERROR` |

## OpenAPI 约定

- `/openapi.json` 必须包含 `components.schemas.ErrorResponse`。
- 常见 `400/401/403/404/422` 响应在 OpenAPI 中统一引用 `ErrorResponse`，便于前端 client 生成或手写类型。
- 新增路由必须声明 `response_model`；若返回列表，优先使用 `{items,total}` 包装，避免前端无法区分页码和全集合。

## Phase 3 核心 API 分组

- Auth：`/api/v1/auth/*`，登录、2FA、refresh、当前用户、密码/API key。
- Assets：`/api/v1/assets/*`，资产、平台、受控连接测试。
- Sessions：`/api/v1/sessions/*`，会话创建/关闭，JIT grant 绑定。
- Workflow/JIT：`/api/v1/workflows/*`，申请、提交、审批、拒绝、撤销、active grant。
- Audit/SIEM：`/api/v1/audits/events`，审计事件创建和检索。
- Tenancy：`/api/v1/tenancy/*`，Phase 4 组织/团队/项目管理与租户隔离 API。

## Phase 4 Tenancy API（#t42）

### PolicyRule 组织/团队/项目绑定

策略决策请求的 `resource` 可携带 `organization_id`、`team_id`、`project_id`。`PolicyRule` 可通过 `organization_ids`、`team_ids`、`project_ids` 将规则绑定到对应资源维度；列表为空表示不限制该维度，列表包含 `*` 表示该维度通配。

安全语义：

- 规则声明了某一维度绑定时，资源必须携带对应 ID 且 ID 必须命中绑定列表。
- 资源维度缺失或不匹配时，该规则不匹配，最终按 deny-by-default 返回 `NO_MATCHING_POLICY`。
- 租户仍由 `tenant_id` 独立约束；组织/团队/项目维度不能放宽跨租户访问。

### GET `/api/v1/tenancy/organizations`

用途：返回当前登录用户可见的 Organization 列表。

鉴权：需要登录态；`admin` 或 `tenancy:read` 权限可访问。后端使用当前用户 `tenant_id` 和 `app.tenancy.scope.scoped_select()` 过滤，不接受前端传入 tenant。

响应 `200`：

```json
{
  "items": [
    {
      "id": "org-a",
      "tenant_id": "tenant-a",
      "name": "Tenant A Ops",
      "status": "active"
    }
  ],
  "total": 1
}
```

安全语义：

- 跨租户 Organization 不出现在响应中。
- 非 admin 用户若绑定了 `organization_id`，只返回该用户可见组织。
- 未授权用户返回 `403` 的统一错误响应。

### POST `/api/v1/tenancy/organizations`

用途：在当前用户租户内创建 Organization。

鉴权：需要登录态和 `admin` 权限；后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "id": "org-a",
  "name": "Tenant A Ops",
  "status": "active"
}
```

响应 `201`：

```json
{
  "id": "org-a",
  "tenant_id": "tenant-a",
  "name": "Tenant A Ops",
  "status": "active"
}
```

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `ORGANIZATION_ALREADY_EXISTS` | 当前租户内组织 ID 已存在 |
| 403 | `TENANT_SCOPE_VIOLATION` | 组织 ID 已被其他租户占用 |
| 403 | `缺少权限: admin` | 当前用户不能创建组织 |

### GET `/api/v1/tenancy/teams`

用途：返回当前登录用户可见的 Team 列表。

鉴权：需要登录态；`admin` 或 `tenancy:read` 权限可访问。后端使用当前用户 `tenant_id` 和 `app.tenancy.scope.scoped_select()` 过滤，不接受前端传入 tenant。

响应 `200`：

```json
{
  "items": [
    {
      "id": "team-a",
      "tenant_id": "tenant-a",
      "organization_id": "org-a",
      "name": "Ops"
    }
  ],
  "total": 1
}
```

安全语义：

- 跨租户 Team 不出现在响应中。
- 非 admin 用户若绑定了 `team_id`，只返回该用户可见团队。
- 未授权用户返回 `403` 的统一错误响应。

### POST `/api/v1/tenancy/teams`

用途：在当前用户租户内、指定 Organization 下创建 Team。

鉴权：需要登录态和 `admin` 权限；后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "id": "team-a",
  "organization_id": "org-a",
  "name": "Ops"
}
```

响应 `201`：

```json
{
  "id": "team-a",
  "tenant_id": "tenant-a",
  "organization_id": "org-a",
  "name": "Ops"
}
```

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `TEAM_ALREADY_EXISTS` | 当前租户内 Team ID 已存在 |
| 403 | `TENANT_SCOPE_VIOLATION` | Organization 或 Team ID 已被其他租户占用 |
| 403 | `缺少权限: admin` | 当前用户不能创建团队 |
| 404 | `ORGANIZATION_NOT_FOUND` | 指定组织不存在 |

### GET `/api/v1/tenancy/projects`

用途：返回当前登录用户可见的 Project 列表。

鉴权：需要登录态；`admin` 或 `tenancy:read` 权限可访问。后端使用当前用户 `tenant_id` 和 `app.tenancy.scope.scoped_select()` 过滤，不接受前端传入 tenant。

响应 `200`：

```json
{
  "items": [
    {
      "id": "project-a",
      "tenant_id": "tenant-a",
      "organization_id": "org-a",
      "team_id": "team-a",
      "name": "Production",
      "status": "active"
    }
  ],
  "total": 1
}
```

安全语义：

- 跨租户 Project 不出现在响应中。
- 非 admin 用户若绑定了 `project_id`，只返回该用户可见项目；若未绑定项目但绑定了 `team_id` 或 `organization_id`，按对应维度继续收敛。
- 未授权用户返回 `403` 的统一错误响应。

### POST `/api/v1/tenancy/projects`

用途：在当前用户租户内、指定 Organization 与可选 Team 下创建 Project。

鉴权：需要登录态和 `admin` 权限；后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "id": "project-a",
  "organization_id": "org-a",
  "team_id": "team-a",
  "name": "Production",
  "status": "active"
}
```

响应 `201`：

```json
{
  "id": "project-a",
  "tenant_id": "tenant-a",
  "organization_id": "org-a",
  "team_id": "team-a",
  "name": "Production",
  "status": "active"
}
```

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `PROJECT_ALREADY_EXISTS` | 当前租户内 Project ID 已存在 |
| 403 | `TENANT_SCOPE_VIOLATION` | Organization、Team 或 Project ID 被其他租户占用，或 Team 不属于指定 Organization |
| 403 | `缺少权限: admin` | 当前用户不能创建项目 |
| 404 | `ORGANIZATION_NOT_FOUND` | 指定组织不存在 |
| 404 | `TEAM_NOT_FOUND` | 指定团队不存在 |

## Session connection token（#t42）

前端创建会话前必须先向后端换取真实 `connection_token`，不得在前端伪造 token。

### POST `/api/v1/sessions/connection-token`

用途：基于已批准且仍有效的 JIT grant 签发短期、一次性会话连接 token。该 token 随后用于 `POST /api/v1/sessions/` 的 `connection_token` 字段。

鉴权：需要登录态；后端使用当前用户 `id` 与 `tenant_id` 作为 token subject/tenant 绑定来源，不接受前端传入 subject/tenant。

请求体：

```json
{
  "jit_grant_id": "grant-1",
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "action": "session.connect"
}
```

响应 `201`：

```json
{
  "connection_token": "jgt_xxx",
  "expires_at": "2026-07-01T12:25:00+00:00",
  "jit_grant_id": "grant-1",
  "workflow_request_id": "wr-1",
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "action": "session.connect"
}
```

安全语义：

- token 最长 5 分钟，并且不会超过 JIT grant 的 `expires_at`。
- token 一次性消费；重复使用返回 `CONNECTION_TOKEN_NOT_FOUND`。
- token 绑定当前用户、租户、资产、账号、协议、动作、JIT grant 和 workflow request。
- JIT grant 必须是 `active`，且 subject/asset/account/protocol/action 全部匹配；过期、撤销、跨用户、跨资产或跨账号均 fail-closed。
- 后端只按 token 摘要索引内部存储；审计事件不记录明文 token。

错误码（当前通过 HTTP `detail` 返回）：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `JIT_GRANT_NOT_FOUND` | grant 不存在或不属于当前租户 |
| 403 | `JIT_GRANT_NOT_ACTIVE:<status>` | grant 已 used/revoked/expired 等 |
| 403 | `JIT_GRANT_EXPIRED` | grant 已过期 |
| 403 | `JIT_GRANT_SUBJECT_MISMATCH` | 当前用户不是 grant subject |
| 403 | `JIT_GRANT_ASSET_MISMATCH` | 资产不匹配 |
| 403 | `JIT_GRANT_ACCOUNT_MISMATCH` | 账号不匹配 |
| 403 | `JIT_GRANT_PROTOCOL_MISMATCH` | 协议不匹配 |
| 403 | `JIT_GRANT_ACTION_MISMATCH` | 动作不匹配 |

### POST `/api/v1/sessions/`

前端必须把上一步响应中的 `connection_token` 原样传给会话创建接口，同时继续传同一个 `jit_grant_id`：

```json
{
  "asset_id": "asset-1",
  "account_id": "root",
  "protocol": "ssh",
  "jit_grant_id": "grant-1",
  "connection_token": "jgt_xxx"
}
```

后端会再次走 JIT grant reserve/consume、PolicyDecisionService 和 connection token consume/绑定校验；任一环节不匹配都拒绝创建会话。

### GET `/api/v1/sessions/`

用途：返回当前登录用户、当前租户下的会话列表，供 Phase 3 会话页展示状态、关闭会话和跳转审计追踪。

鉴权：需要登录态；后端使用当前用户 `id` 与 `tenant_id` 过滤，不接受前端传 subject/tenant。

响应 `200`：

```json
{
  "items": [
    {
      "id": "session-1",
      "asset_id": "asset-1",
      "account_id": "root",
      "connector_id": "connector-1",
      "protocol": "ssh",
      "status": "active",
      "connection_url": "wss://connector.example/sessions/session-1",
      "workflow_request_id": "wr-1",
      "jit_grant_id": "grant-1",
      "created_at": "2026-07-01T12:25:00+00:00",
      "updated_at": "2026-07-01T12:25:00+00:00",
      "closed_at": null,
      "audit_event_ids": ["audit-1"]
    }
  ],
  "total": 1
}
```

安全语义：

- 只返回当前用户自己的会话；其他 subject 的会话不得出现在响应中。
- 只返回当前租户会话；跨租户数据 fail-closed 为空集合。
- 列表按 `created_at` 倒序，便于控制台优先展示最新会话。

## 前端解析建议

1. 先读 `code` 做流程判断；业务码优先于 HTTP 状态。
2. 展示文案优先 `message`，必要时展开 `detail`。
3. 422 表单错误按 `detail[].loc` 映射字段。
4. 不要依赖未声明字段；新增字段必须先进入 OpenAPI 和本文件。
