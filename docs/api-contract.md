# Phase 3 API 契约与错误码规范

> 面向 #t36 前端控制台、#t38 E2E smoke 和 #t41 QA 门禁。本文件记录当前后端稳定契约，后续新增 API 默认沿用。

更新时间：2026-07-04
范围：Phase 3 MVP 前端/后端联调契约，以及 Phase 4 多租户增量契约。

## 基础约定

- API 前缀：业务接口统一在 `/api/v1/*` 下暴露；健康检查保留 `/health`。
- 认证：默认使用 Bearer access token；未认证或 token 失效返回 `401`。
- 租户/权限：接口必须通过 `current_user` / `require_permission` 或业务层 actor-aware 查询控制租户与资源边界。Phase 4 起 `current_user` 会返回 `tenant_id`，以及可选的 `organization_id`、`team_id`、`project_id`；新增 DB 查询优先使用 `app.tenancy.scope.scoped_select()` 注入租户过滤。
- Connector 信任：连接器注册、心跳、key rotation 和 connection token 签发必须 fail-closed。Phase 4 #t45 起 registry 会维护 `last_heartbeat_at` 租约，过期连接器不得签发 connection token；enrollment token 可绑定 mTLS 证书指纹，签发 token 时 presented fingerprint 必须与注册记录一致；enrollment token 也可绑定 attestation nonce/digest，注册请求必须携带匹配声明；active connector 可轮换 public key fingerprint，并记录 previous/current fingerprint 与轮换时间；后端 `ConnectorSdkClient` 封装持久化 Connector 管理 API，SDK 异常不得泄露 bearer token。
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
- Accounts：`/api/v1/accounts/*`，Phase 4 资产账号托管与 Vault secret 引用。
- Sessions：`/api/v1/sessions/*`，会话创建/关闭，JIT grant 绑定。
- Workflow/JIT：`/api/v1/workflows/*`，申请、提交、审批、拒绝、撤销、active grant。
- Audit/SIEM：`/api/v1/audits/events`，审计事件创建和检索。
- Tenancy：`/api/v1/tenancy/*`，Phase 4 组织/团队/项目管理与租户隔离 API。
- Session Recordings：`/api/v1/sessions/{session_id}/recordings` 与 `/api/v1/session-recordings/*`，Phase 4 会话录制元数据、命令事件上报与命令检索。
- Webhook Endpoints：`/api/v1/webhook-endpoints/*`，Phase 4 WebHook / 通知中心 endpoint 管理基础。
- Notification Rules：`/api/v1/notification-rules/*`，Phase 4 WebHook / 通知规则管理基础。
- Notification Deliveries：`/api/v1/notification-rules/{rule_id}/deliveries` 与 `/api/v1/notification-deliveries/*`，Phase 4 WebHook 可靠投递队列基础。

## Phase 4 Webhook Endpoint API（#t47）

### POST `/api/v1/notification-rules/{rule_id}/deliveries`

用途：将当前租户 active 通知规则匹配的事件写入可靠投递队列，供后续 worker 重试和死信处理。

鉴权：需要登录态；`admin` 或 `notifications:write` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "event_type": "audit.event.created",
  "payload": {
    "audit_event_id": "evt-1"
  }
}
```

响应 `202`：

```json
{
  "id": 1,
  "tenant_id": "tenant-a",
  "notification_rule_id": 1,
  "webhook_endpoint_id": 1,
  "event_type": "audit.event.created",
  "status": "pending",
  "attempts": 0,
  "next_attempt_at": "2026-07-04T05:00:00Z",
  "last_error": null,
  "created_at": "2026-07-04T05:00:00Z",
  "updated_at": "2026-07-04T05:00:00Z"
}
```

安全语义：

- 只能写入当前租户 active rule 及其 active WebHook endpoint；缺失、跨租户或 disabled rule 返回 `404 NOTIFICATION_RULE_NOT_FOUND`。
- `event_type` 必须同时存在于 rule 和 endpoint 的 `event_types`，否则返回 `400 NOTIFICATION_EVENT_NOT_ALLOWED`。
- payload 入库前会脱敏 token/password/secret/credential 等敏感键或赋值片段；响应不返回 payload。
- 当前切片只落队列记录，不执行外部 HTTP/IM 投递。

### GET `/api/v1/notification-deliveries/`

用途：返回当前租户可见的通知投递队列记录，按 ID 升序返回 `{items,total}`。

鉴权：需要登录态；`admin` 或 `notifications:read` 权限可访问。响应不返回 payload、signing secret、连接 token 或外部投递凭据。

### POST `/api/v1/notification-rules/`

用途：在当前租户内创建一条事件通知规则，将事件类型绑定到已配置的 active WebHook endpoint。

鉴权：需要登录态；`admin` 或 `notifications:write` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "name": "recording-closed-to-siem",
  "event_types": ["session.recording.closed"],
  "webhook_endpoint_id": 1
}
```

响应 `201`：

```json
{
  "id": 1,
  "tenant_id": "tenant-a",
  "name": "recording-closed-to-siem",
  "event_types": ["session.recording.closed"],
  "webhook_endpoint_id": 1,
  "webhook_endpoint_name": "security-siem",
  "status": "active",
  "created_at": "2026-07-04T04:30:00Z",
  "updated_at": "2026-07-04T04:30:00Z"
}
```

安全语义：

- 规则只能引用当前租户 active WebHook endpoint；跨租户、缺失或 disabled endpoint 返回 `404 WEBHOOK_ENDPOINT_NOT_FOUND`。
- 响应不返回 WebHook signing secret 明文、摘要、连接 token 或任何外部投递凭据。
- 当前切片只落规则管理基础，不执行外部通知投递。

### GET `/api/v1/notification-rules/`

用途：返回当前租户可见的通知规则列表。

鉴权：需要登录态；`admin` 或 `notifications:read` 权限可访问。响应按 rule ID 升序返回 `{items,total}`。

### POST `/api/v1/webhook-endpoints/`

用途：在当前租户内创建一个用于后续通知、SIEM 或工单集成的 HTTPS webhook endpoint。

鉴权：需要登录态；`admin` 或 `webhooks:write` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "name": "security-siem",
  "url": "https://siem.example.test/janusgate",
  "event_types": ["session.recording.closed", "audit.event.created"],
  "signing_secret": "super-secret-webhook-key"
}
```

响应 `201`：

```json
{
  "id": 1,
  "tenant_id": "tenant-a",
  "name": "security-siem",
  "url": "https://siem.example.test/janusgate",
  "event_types": ["session.recording.closed", "audit.event.created"],
  "status": "active",
  "signing_secret_configured": true,
  "created_at": "2026-07-04T04:00:00Z",
  "updated_at": "2026-07-04T04:00:00Z"
}
```

安全语义：

- signing secret 只以 digest 形式持久化；响应不返回 signing secret 明文或摘要。
- endpoint URL 必须使用 `https://`，明文 HTTP 返回 `400 INVALID_WEBHOOK_URL`。
- 只允许创建当前租户 endpoint；跨租户读取不会返回该 endpoint。
- 当前切片只落 endpoint 管理基础，不执行外部通知投递。

### GET `/api/v1/webhook-endpoints/`

用途：返回当前租户可见的 webhook endpoint 列表。

鉴权：需要登录态；`admin` 或 `webhooks:read` 权限可访问。响应按 endpoint ID 升序返回 `{items,total}`。

## Phase 4 Session Recording API（#t46）

### POST `/api/v1/sessions/{session_id}/recordings`

用途：为当前租户下的一条会话创建录制元数据记录。

鉴权：需要登录态；`admin` 或 `session-recordings:write` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "asset_id": "asset-1",
  "account_id": "account-1",
  "protocol": "ssh",
  "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast"
}
```

响应 `201`：

```json
{
  "id": 1,
  "tenant_id": "tenant-a",
  "session_id": "session-a",
  "subject_id": "user-1",
  "asset_id": "asset-1",
  "account_id": "account-1",
  "protocol": "ssh",
  "status": "recording",
  "storage_uri": "s3://janusgate-recordings/tenant-a/session-a.cast",
  "started_at": "2026-07-04T01:00:00Z",
  "ended_at": null
}
```

### POST `/api/v1/session-recordings/{recording_id}/commands`

用途：向当前租户可见的录制追加一条命令审计事件。

鉴权：需要登录态；`admin` 或 `session-recordings:write` 权限可访问。

请求体：

```json
{
  "sequence": 1,
  "command": "sudo systemctl restart nginx",
  "exit_code": 0,
  "output_excerpt": "token=raw-secret"
}
```

安全语义：

- 只允许追加当前租户可见录制；跨租户或不存在录制返回 `404 SESSION_RECORDING_NOT_FOUND`。
- `output_excerpt` 返回前会脱敏 `token=`、`password=`、`secret=`、`credential=` 赋值片段。
- 当前切片只保存命令事件和摘要；真实录制对象存储、实时流和回放 UI 后续补齐。

### POST `/api/v1/connectors/{connector_id}/session-recordings/{recording_id}/commands`

用途：连接器/边缘网关向当前租户可见的录制实时上报命令审计事件。

鉴权：需要登录态；`admin` 或 `connectors:write` 权限可访问。后端会同时校验 connector 和 recording 均属于当前用户租户。

请求体沿用 `SessionCommandEventCreate`：

```json
{
  "sequence": 2,
  "command": "whoami",
  "exit_code": 0,
  "output_excerpt": "password=raw-secret"
}
```

安全语义：

- connector 不存在或跨租户时返回 `404 CONNECTOR_NOT_FOUND`。
- connector 为 `inactive` / `revoked` 时返回 `403 CONNECTOR_NOT_ACTIVE`。
- recording 不存在、跨租户或已关闭时返回 `404 SESSION_RECORDING_NOT_FOUND`。
- 响应沿用命令事件脱敏规则，不返回 connector 私钥、连接 token、凭据或对象存储签名 URL。

### GET `/api/v1/session-recordings/{recording_id}/commands`

用途：返回当前租户可见录制的命令时间线，作为后续回放 UI 的只读数据源。

鉴权：需要登录态；`admin` 或 `session-recordings:read` 权限可访问。响应按 `sequence` 升序返回 `{items,total}`。

安全语义：

- 只允许读取当前租户可见录制；跨租户或不存在录制返回 `404 SESSION_RECORDING_NOT_FOUND`。
- 响应沿用命令事件脱敏后的 `output_excerpt`，不返回凭据、连接 token 或对象存储签名 URL。
- 前端 `/sessions` 使用该接口按 Recording ID 加载只读回放命令时间线；当前不要求后端提供按 session 自动发现 recording 的额外契约。

### POST `/api/v1/session-recordings/{recording_id}/close`

用途：关闭当前租户可见且仍处于 `recording` 状态的录制，写入 `ended_at` 并返回录制元数据。

鉴权：需要登录态；`admin` 或 `session-recordings:write` 权限可访问。

安全语义：

- 只允许关闭当前租户可见录制；跨租户、不存在或已关闭录制统一返回 `404 SESSION_RECORDING_NOT_FOUND`。
- 响应不包含命令输出、凭据、连接 token 或对象存储签名 URL。

### GET `/api/v1/session-recordings/commands?query=nginx`

用途：按关键词检索当前租户内的命令事件。

鉴权：需要登录态；`admin` 或 `session-recordings:read` 权限可访问。响应按命令发生时间倒序返回 `{items,total}`。

搜索语义：

- PostgreSQL 环境使用 `to_tsvector('simple', command || ' ' || output_excerpt) @@ plainto_tsquery('simple', query)`，并通过 `ix_session_command_events_search_vector` GIN 索引优化命令与输出摘要全文检索。
- 非 PostgreSQL 测试环境保留 `ILIKE` fallback，便于 SQLite 单元测试覆盖相同租户隔离与脱敏响应契约。
- 排序使用 `occurred_at DESC, id DESC`，并声明 `ix_session_command_events_tenant_occurred_id` 支撑同租户倒序读取。

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

## Phase 4 Account Custody API（#t43）

### GET `/api/v1/accounts/`

用途：返回当前登录用户可见的资产账号托管记录。

鉴权：需要登录态；`admin` 或 `accounts:read` 权限可访问。后端使用当前用户 `tenant_id` 与 `app.tenancy.scope.scoped_select()` 过滤；非 admin 用户若绑定了 `project_id`、`team_id` 或 `organization_id`，按最细维度收敛。

响应 `200`：

```json
{
  "items": [
    {
      "id": 1,
      "tenant_id": "tenant-a",
      "asset_id": 1,
      "username": "deploy",
      "protocol": "ssh",
      "secret_id": "sec_tenant_a_deploy",
      "organization_id": "org-a",
      "team_id": "team-a",
      "project_id": "project-a",
      "status": "active",
      "rotation_policy": "manual"
    }
  ],
  "total": 1
}
```

安全语义：

- 跨租户 Account 不出现在响应中。
- 响应只返回 Vault `secret_id` 引用，不返回密码、私钥或 token 明文。
- 未授权用户返回 `403` 的统一错误响应。

### POST `/api/v1/accounts/`

用途：在当前用户租户内创建资产账号托管记录。

鉴权：需要登录态；`admin` 或 `accounts:write` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "asset_id": 1,
  "username": "deploy",
  "protocol": "ssh",
  "secret_id": "sec_tenant_a_deploy",
  "organization_id": "org-a",
  "team_id": "team-a",
  "project_id": "project-a",
  "status": "active",
  "rotation_policy": "manual"
}
```

响应 `201`：同 `GET /api/v1/accounts/` 的单条 item。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `TENANT_SCOPE_VIOLATION` | Organization、Team 或 Project 不属于当前租户或层级不匹配 |
| 403 | `缺少权限: accounts:write` | 当前用户不能创建账号 |
| 404 | `ASSET_NOT_FOUND` | 指定资产不存在 |
| 404 | `ORGANIZATION_NOT_FOUND` | 指定组织不存在 |
| 404 | `TEAM_NOT_FOUND` | 指定团队不存在 |
| 404 | `PROJECT_NOT_FOUND` | 指定项目不存在 |

### GET `/api/v1/accounts/{account_id}/rotations`

用途：返回当前登录用户可见账号的凭据轮换调度记录。

鉴权：需要登录态；`admin` 或 `accounts:read` 权限可访问。后端先按 `Account` 的租户/项目可见范围确认账号存在，再返回该账号下的 rotation 记录。

响应 `200`：

```json
{
  "items": [
    {
      "id": 1,
      "tenant_id": "tenant-a",
      "account_id": 1,
      "status": "scheduled",
      "reason": "quarterly rotation",
      "requested_by": "user-1",
      "scheduled_at": "2026-07-04T10:00:00Z"
    }
  ],
  "total": 1
}
```

安全语义：

- 不返回 `secret_id`、密码、私钥或 token 明文。
- 不可见或跨租户账号统一返回 `404 ACCOUNT_NOT_FOUND`，避免泄露账号存在性。

### POST `/api/v1/accounts/{account_id}/rotations`

用途：为当前登录用户可见账号创建凭据轮换调度记录。

鉴权：需要登录态；`admin` 或 `accounts:rotate` 权限可访问。后端 `CredentialRotationWorker` 会处理到期且状态为 `scheduled` 的记录；worker 成功后将对应 `Account.secret_id` 更新为轮换器返回的新 secret 引用，并把 rotation 标记为 `completed`，同时在内部记录 `previous_secret_id` 与 `new_secret_id` 以支持 completed rotation 回滚；轮换器失败时标记为 `failed`、记录错误码且不改账号 secret。轮换响应仍不返回 secret 引用、密码、私钥或 token 明文。

请求体：

```json
{
  "reason": "quarterly rotation",
  "scheduled_at": "2026-07-04T10:00:00Z"
}
```

响应 `201`：同 `GET /api/v1/accounts/{account_id}/rotations` 的单条 item。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `缺少权限: accounts:rotate` | 当前用户不能调度凭据轮换 |
| 404 | `ACCOUNT_NOT_FOUND` | 指定账号不存在或当前用户不可见 |

## Phase 4 SSH CA / Temporary Certificate Service（#t44）

当前 #t44 已落地后端模型、CA 管理 API、CA 禁用 API、连接器信任 bundle API、服务契约、临时证书签发/撤销 REST API、接入 API 路由的 Vault-backed OpenSSH signer，以及前端 `/ssh-ca` SSH CA / 临时证书入口。前端入口必须沿用本节安全语义。

核心模型：

- `SshCertificateAuthority`：按租户保存 CA 名称、公钥、`private_key_secret_id`、状态与默认有效期。私钥明文不得进入数据库或响应，只能通过 Vault secret 引用交给签名器。
- `Asset.trusted_ssh_ca_id`：资产信任的 SSH CA 引用；未绑定或不匹配时不得签发临时证书。
- `SshCertificate`：保存租户、CA、资产、账号、principal、公钥、serial、证书正文、有效期、签发人、状态与撤销信息。

服务语义：

- `SshCertificateService.issue_certificate(...)` 只允许同租户 active CA、active Asset、active SSH Account 组合签发。
- `VaultOpenSshCertificateSigner` 必须只通过 `private_key_secret_id` 从 SecretProvider unwrap CA 私钥，并生成 OpenSSH user certificate；API 路由默认使用该 signer。CA 私钥缺失、撤销或格式错误时 fail-closed 为 `SSH_CA_PRIVATE_KEY_UNAVAILABLE`。
- 资产必须显式信任请求中的 CA，否则 fail-closed 为 `ASSET_SSH_CA_NOT_TRUSTED`。
- 跨租户或不可见资产返回 `ASSET_NOT_FOUND`；跨租户或不可见账号返回 `ACCOUNT_NOT_FOUND`；inactive 或不存在的 CA 返回 `SSH_CA_NOT_FOUND`。
- 签发请求只向 signer 传 `private_key_secret_id`，不传或记录私钥明文。
- `SshCertificateService.revoke_certificate(...)` 只撤销同租户且状态为 `issued` 的证书；重复撤销、跨租户或不存在证书返回 `False`。

已暴露 API：

### GET `/api/v1/ssh-certificate-authorities/`

用途：返回当前登录用户租户内的 SSH CA 列表。

鉴权：需要登录态；`admin` 或 `ssh-certificate-authorities:read` 权限可访问。

响应只返回 CA 名称、公钥、状态和默认有效期，不返回 `private_key_secret_id` 或任何私钥材料。

### POST `/api/v1/ssh-certificate-authorities/`

用途：在当前用户租户内创建 SSH CA，私钥材料必须先存入 Vault/KMS，API 只接收 `private_key_secret_id` 引用。

鉴权：需要登录态；`admin` 或 `ssh-certificate-authorities:create` 权限可访问。

请求体：

```json
{
  "name": "tenant-a-ca",
  "public_key": "ssh-ed25519 AAAA...",
  "private_key_secret_id": "sec_tenant_a_ssh_ca",
  "validity_seconds": 900
}
```

响应 `201` 返回 CA 元数据；响应不包含 `private_key_secret_id` 或任何私钥明文。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `SSH_CA_ALREADY_EXISTS` | 当前租户内 CA 名称已存在 |
| 403 | `缺少权限: ssh-certificate-authorities:create` | 当前用户不能创建 CA |

### POST `/api/v1/ssh-certificate-authorities/{authority_id}/disable`

用途：禁用当前租户内仍处于 `active` 状态的 SSH CA，后续签发请求不得再使用该 CA。

鉴权：需要登录态；`admin` 或 `ssh-certificate-authorities:disable` 权限可访问。

响应 `200` 返回禁用后的 CA 元数据，`status` 为 `disabled`；响应不包含 `private_key_secret_id` 或任何私钥明文。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `缺少权限: ssh-certificate-authorities:disable` | 当前用户不能禁用 CA |
| 404 | `SSH_CA_NOT_FOUND` | CA 不存在、跨租户或已不处于 active 状态 |

### GET `/api/v1/ssh-certificate-authorities/trust-bundle`

用途：返回当前登录用户租户内 active 资产实际信任的 active SSH CA 公钥 bundle，供连接器/资产侧同步 trusted CA 公钥。

鉴权：需要登录态；`admin` 或 `ssh-certificate-authorities:read` 权限可访问。

响应 `200`：

```json
{
  "items": [
    {
      "ca_id": 1,
      "tenant_id": "tenant-a",
      "name": "tenant-a-ca",
      "public_key": "ssh-ed25519 AAAA...",
      "trusted_asset_ids": [1, 3]
    }
  ],
  "total": 1
}
```

安全语义：

- 只返回当前租户 active CA 与 active Asset 的交集。
- 未被任何 active 资产信任的 CA、disabled CA、跨租户 CA、inactive 资产引用的 CA 不返回。
- 响应不包含 `private_key_secret_id` 或任何私钥材料。

### GET `/api/v1/ssh-certificates/`

用途：返回当前登录用户租户内可见的临时 SSH 证书列表。

鉴权：需要登录态；`admin` 或 `ssh-certificates:read` 权限可访问。

响应只返回证书正文、公钥、serial、有效期和撤销状态等证书材料，不返回 CA 私钥明文或 CA 私钥 secret 引用。

### POST `/api/v1/ssh-certificates/`

用途：基于 active CA、active Asset 和 active SSH Account 签发短期临时证书。

鉴权：需要登录态；`admin` 或 `ssh-certificates:issue` 权限可访问。

请求体：

```json
{
  "ca_id": 1,
  "asset_id": 1,
  "account_id": 1,
  "principal": "deploy",
  "public_key": "ssh-ed25519 AAAA..."
}
```

响应 `201` 返回证书记录和 `certificate_body`；响应不包含 `private_key_secret_id` 或任何私钥明文。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `SSH_CA_PRIVATE_KEY_UNAVAILABLE` | CA 私钥 secret 缺失、撤销、解密失败或格式不可用 |
| 400 | `SSH_PUBLIC_KEY_INVALID` | 请求公钥不是可解析的 SSH 公钥 |
| 403 | `ASSET_SSH_CA_NOT_TRUSTED` | 资产未显式信任请求 CA |
| 403 | `缺少权限: ssh-certificates:issue` | 当前用户不能签发证书 |
| 404 | `SSH_CA_NOT_FOUND` | CA 不存在、inactive 或不属于当前租户 |
| 404 | `ASSET_NOT_FOUND` | 资产不存在、inactive 或不属于当前租户 |
| 404 | `ACCOUNT_NOT_FOUND` | 账号不存在、不是 SSH、inactive、资产不匹配或不在当前用户可见范围 |

### POST `/api/v1/ssh-certificates/{certificate_id}/revoke`

用途：撤销当前租户内仍处于 `issued` 状态的临时证书。

鉴权：需要登录态；`admin` 或 `ssh-certificates:revoke` 权限可访问。

请求体：

```json
{
  "reason": "access ended"
}
```

响应 `200` 返回撤销后的证书记录，`status` 为 `revoked`，并写入 `revoked_at` 与 `revoke_reason`。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `缺少权限: ssh-certificates:revoke` | 当前用户不能撤销证书 |
| 404 | `SSH_CERTIFICATE_NOT_FOUND` | 证书不存在、跨租户或已不处于 issued 状态 |

后续切片可继续增强：

- 前端临时证书签发交互与 CA 创建/禁用操作。
- 连接器生产级 trust bundle 同步与信任链轮换。

## Phase 4 Connector Trust Chain（#t45）

当前 #t45 已落地 Connector Registry 心跳租约、过期 fail-closed token 签发检查、mTLS 证书指纹绑定、enrollment-token 绑定的 attestation nonce/digest 注册校验、active connector public key rotation、租户隔离的持久化 Connector 管理 API，以及覆盖 create/heartbeat/rotate-key 的轻量 Connector SDK。

安全语义：

- Connector enrollment token 仍只保存 digest，不保存明文 token。
- Enrollment token 可绑定 `mtls_certificate_fingerprint`；注册请求必须携带相同指纹，否则返回 `ENROLLMENT_TOKEN_BINDING_MISMATCH`。
- Enrollment token 可绑定 `attestation_nonce` 与 `attestation_digest`；注册请求缺失 attestation 时返回 `CONNECTOR_ATTESTATION_REQUIRED`，nonce/digest 不匹配时返回 `CONNECTOR_ATTESTATION_MISMATCH`。
- 注册成功后 Connector 记录保存绑定的 mTLS 证书指纹；未由 enrollment token 绑定的连接器不启用运行时 mTLS 指纹校验。
- 注册成功后 Connector 记录保存 attestation nonce/digest 引用；registry 不保存 attestation 原始文档、设备私钥或平台证明材料。
- active Connector 可轮换 `public_key_fingerprint`，registry 会保留 `previous_public_key_fingerprint` 与 `key_rotated_at`；inactive/revoked Connector 不允许轮换，缺失或非法 `sha256:` 指纹返回 `INVALID_CONNECTOR_FINGERPRINT`。
- 签发 connection token 前，若 Connector 记录存在 mTLS 证书指纹，调用方必须提供相同 presented certificate fingerprint；缺失或不匹配返回 `CONNECTOR_MTLS_CERTIFICATE_MISMATCH`。
- mTLS 指纹只作为证书绑定引用进入 registry 记录；不得记录证书私钥、client certificate 明文或 TLS session secret。
- 持久化管理 API 只返回 `mtls_bound` 与 `attestation_bound` 布尔值，不返回 enrollment token、attestation digest、私钥或长期凭据。
- Connector SDK 只注入 `Authorization: Bearer <token>` 请求头，不把 access token 写入 `ConnectorSdkError` 的消息、`code` 或 `detail`；API 失败时保留 HTTP status 与统一错误码供调用方处理。

### GET `/api/v1/connectors/`

用途：返回当前租户下的 Connector 运行态列表。

鉴权：需要登录态；`admin` 或 `connectors:read` 权限可访问。后端使用当前用户 `tenant_id` 过滤，不接受前端传入 tenant。

响应 `200`：

```json
{
  "items": [
    {
      "id": 1,
      "tenant_id": "tenant-a",
      "name": "koko-prod-1",
      "environment": "prod",
      "public_key_fingerprint": "sha256:connector-key",
      "previous_public_key_fingerprint": null,
      "capabilities": ["ssh", "database"],
      "status": "active",
      "mtls_bound": true,
      "attestation_bound": false,
      "registered_at": "2026-07-04T00:00:00Z",
      "last_heartbeat_at": null,
      "key_rotated_at": null
    }
  ],
  "total": 1
}
```

### POST `/api/v1/connectors/`

用途：在当前租户内创建持久化 Connector 记录，供控制台管理和 Connector SDK 调用。

鉴权：需要登录态；`admin` 或 `connectors:write` 权限可访问。

请求体：

```json
{
  "name": "koko-prod-1",
  "environment": "prod",
  "public_key_fingerprint": "sha256:connector-key",
  "mtls_certificate_fingerprint": "sha256:client-cert",
  "capabilities": ["ssh", "database"],
  "status": "active"
}
```

响应 `201`：同 `GET /api/v1/connectors/` 的单条 item。响应不返回 `mtls_certificate_fingerprint`、`attestation_nonce`、`attestation_digest`、enrollment token 或私钥材料。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `INVALID_CONNECTOR_FINGERPRINT` | Connector public key fingerprint 不是 `sha256:` 格式 |
| 400 | `INVALID_CONNECTOR_MTLS_FINGERPRINT` | mTLS certificate fingerprint 不是 `sha256:` 格式 |
| 403 | `缺少权限: connectors:write` | 当前用户不能创建或更新 Connector |

### POST `/api/v1/connectors/{connector_id}/heartbeat`

用途：刷新当前租户内 active Connector 的 `last_heartbeat_at`。

鉴权：需要登录态；`admin` 或 `connectors:write` 权限可访问。

响应 `200`：同 `GET /api/v1/connectors/` 的单条 item。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 403 | `CONNECTOR_NOT_ACTIVE` | Connector 已 inactive/revoked，不允许通过 heartbeat 恢复 |
| 404 | `CONNECTOR_NOT_FOUND` | Connector 不存在或不属于当前租户 |

### POST `/api/v1/connectors/{connector_id}/rotate-key`

用途：轮换当前租户内 active Connector 的 public key fingerprint，并记录 `previous_public_key_fingerprint` 与 `key_rotated_at`。

鉴权：需要登录态；`admin` 或 `connectors:write` 权限可访问。

请求体：

```json
{
  "public_key_fingerprint": "sha256:new-key"
}
```

响应 `200`：同 `GET /api/v1/connectors/` 的单条 item。

错误码：

| HTTP | detail | 说明 |
| --- | --- | --- |
| 400 | `INVALID_CONNECTOR_FINGERPRINT` | 新 public key fingerprint 不是 `sha256:` 格式 |
| 403 | `CONNECTOR_NOT_ACTIVE` | Connector 已 inactive/revoked，不允许轮换 |
| 404 | `CONNECTOR_NOT_FOUND` | Connector 不存在或不属于当前租户 |

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

## Phase 4 Connector Registry（#t45）

当前 #t45 已落地 Connector Registry 心跳租约基础能力。连接器注册成功后会记录 `registered_at` 与初始 `last_heartbeat_at`；连接器运行期间通过 registry `record_heartbeat(connector_id)` 刷新 `last_heartbeat_at` 并保持 active 状态。

安全语义：

- connection token 签发前必须同时校验 connector 存在、状态为 `active`、心跳租约未过期，并通过 PolicyDecisionService。
- 已撤销或 inactive connector 不得通过 heartbeat 恢复为 active。
- 心跳过期时 fail-closed，registry 抛出稳定业务码 `CONNECTOR_HEARTBEAT_EXPIRED`。
- 后续公开 REST/Connector API 时不得把 enrollment token 明文、私钥材料或长期凭据返回给连接器或控制台。

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
5. `/accounts` 控制台页只消费 `secret_id` 引用和 rotation 状态，不展示或缓存凭据明文；调度轮换时调用 `POST /api/v1/accounts/{account_id}/rotations` 并使用固定业务原因或后续表单输入。
