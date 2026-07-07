# Phase 3 API 契约与错误码规范

> 面向 #t36 前端控制台、#t38 E2E smoke 和 #t41 QA 门禁。本文件记录当前后端稳定契约，后续新增 API 默认沿用。

更新时间：2026-07-06
范围：Phase 3 MVP 前端/后端联调契约，Phase 4 多租户增量契约，以及 Phase 5 生产化增量契约。

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
- Approval Policies：`/api/v1/workflows/approval-policies`，Phase 4 JIT 策略模板 / 审批策略基础管理与当前租户策略模拟。
- Audit/SIEM：`/api/v1/audits/events`、`/api/v1/audits/reports/summary` 与 `/api/v1/audits/reports/compliance`，审计事件创建、检索、当前租户报表汇总和合规报表导出基础；合规报表响应包含 append-only WORM 归档元数据，不包含原始审计 metadata/message/resource/session 明细。
- Tenancy：`/api/v1/tenancy/*`，Phase 4 组织/团队/项目管理与租户隔离 API。
- Session Recordings：`/api/v1/sessions/{session_id}/recordings` 与 `/api/v1/session-recordings/*`，Phase 4 会话录制元数据、命令事件上报与命令检索。
- Webhook Endpoints：`/api/v1/webhook-endpoints/*`，Phase 4 WebHook / 通知中心 endpoint 管理基础。
- Notification Rules：`/api/v1/notification-rules/*`，Phase 4 WebHook / 通知规则管理基础。
- Notification Deliveries：`/api/v1/notification-rules/{rule_id}/deliveries` 与 `/api/v1/notification-deliveries/*`，Phase 4 WebHook 可靠投递队列基础；`NotificationDeliveryWorker` 负责到期投递、失败重试和 dead-letter 状态推进，`HttpWebhookNotificationSender` 负责向 HTTPS WebHook endpoint 投递已脱敏 payload。
- Admin：`/api/v1/admin/license-summary` 与 `/api/v1/admin/license-config`，Phase 5 #t58 Edition / License 边界摘要和 admin-only 持久化配置 foundation，不返回 license key、签名 secret、外部 validation token 或任何商业密钥材料。

## Phase 5 Edition / License Boundary（#t58）

当前切片提供后端最小 feature flag 与 license 摘要基础。`JANUSGATE_EDITION=community|enterprise` 配置期望版本；默认 `community` 不需要 license，只启用 `core_pam`、`workflow_jit` 和 `audit_reports` 基础能力。`enterprise` 版本只有在 `JANUSGATE_LICENSE_KEY` 通过配置的验签器校验、payload edition 为 `enterprise`、且 `expires_at` 未过期时才生效；缺失、非法、过期或签名不匹配时 fail-closed，`effective_edition` 回退为 `community`，enterprise feature 保持 disabled。当前支持 `JANUSGATE_LICENSE_VERIFIER=hmac` + `JANUSGATE_LICENSE_SIGNING_SECRET` 的 HMAC-SHA256 验签、`JANUSGATE_LICENSE_VERIFIER=ed25519` + `JANUSGATE_LICENSE_PUBLIC_KEY` 的离线公钥验签，以及 `JANUSGATE_LICENSE_VERIFIER=external-http` + `JANUSGATE_LICENSE_VALIDATION_URL` 的外部商业授权服务验证 foundation；Ed25519 模式不要求部署环境保存签名私钥，external-http 模式只向 HTTPS validation endpoint 发送 opaque license key。

### GET `/api/v1/admin/license-summary`

用途：返回当前激活配置下的版本、license 状态和启用/禁用 feature 列表。若 DB 中存在 admin 持久化配置则优先使用该配置；否则回退到当前进程环境变量。前端 `/settings` 已提供最小 License lifecycle 表单，可调用 `POST /api/v1/admin/license-config` 激活持久化配置，并可选择 `hmac`、`ed25519` 或 `external-http` verifier。

鉴权：需要登录态和 `admin` 权限。

响应 `200`：

```json
{
  "configured_edition": "enterprise",
  "effective_edition": "community",
  "license_status": "invalid",
  "enabled_features": ["audit_reports", "core_pam", "workflow_jit"],
  "disabled_features": ["admin_console", "edition_feature_flags", "license_management"],
  "expires_at": null
}
```

安全语义：

- 响应不返回 `JANUSGATE_LICENSE_KEY`、`JANUSGATE_LICENSE_SIGNING_SECRET`、签名值或原始 license payload。
- 响应不返回 `JANUSGATE_LICENSE_PUBLIC_KEY`、`JANUSGATE_LICENSE_VALIDATION_TOKEN` 或任何签名私钥材料。
- `enterprise` 配置没有有效 license 时不能启用 enterprise feature。
- `POST /api/v1/admin/license-config` 可由 `admin` 持久化当前激活 license 配置，请求字段为 `configured_edition`、`license_verifier`、`license_key`、`license_signing_secret` 与 `license_public_key`；`license_verifier` 支持 `hmac`、`ed25519` 和 `external-http`。响应复用 `LicenseSummary`，不回显 license key、signing secret、公钥、validation token 或原始 payload。成功写入会追加 `admin.license_config.updated` 审计事件，metadata 只包含 configured/effective edition、license status、verifier、license key / signing material / public key 是否已配置和 enabled feature 摘要；external-http 的 validation endpoint 和 service token 仅从环境读取，不写入 license 配置表。

## Phase 4 Automation Worker Queue（#t52）

当前切片定义后端 worker 队列写入、单轮消费循环、最小调度 API、`asset.scan` worker handler、`credential.rotate` worker handler 与 `ansible.playbook` worker handler 契约。`AutomationJobQueue` 使用 Redis Streams 风格 `xadd` 写入 `janusgate:automation:jobs`，字段均为字符串，payload 以 `payload_json` 保存，并显式标记 `payload_format=json`。`AutomationWorker` 通过 Redis Streams consumer group 读取消息，按 `job_type` 分发到显式注册的 handler，并仅在 handler 成功后 ack。`AssetScanWorkerHandler` 消费 `asset.scan` 消息时会按当前租户和 active 状态确认资产存在，并只把资产 ID、租户、名称、地址、端口和平台 ID 传给扫描执行器，不传递 legacy credential 字段。`CredentialRotateWorkerHandler` 消费 `credential.rotate` 消息时会按当前租户和 active account 边界确认账号存在，创建 `CredentialRotation` 记录并调用显式改密执行器；队列 payload 只携带 account id 与可选 reason，不携带 secret 引用之外的凭据材料。`AnsiblePlaybookWorkerHandler` 消费 `ansible.playbook` 消息时会按当前租户和 active asset 边界确认全部目标资产存在，并只把 playbook 名称、check mode、请求人和不含 legacy credential 的目标摘要传给显式 runner 契约。`LocalAnsiblePlaybookRunner` 会把 playbook 名称收敛到配置的 playbook root 内相对 `.yml/.yaml` 文件，渲染不含凭据的临时 JSON inventory，在临时 runtime 目录执行 `ansible-playbook`，仅传递去敏后的基础环境变量，并通过 `ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS` 限制执行时间；超时时会返回 `ANSIBLE_PLAYBOOK_TIMED_OUT` 并回收本地子进程。后端可通过 `ANSIBLE_PLAYBOOK_ROOT`、`ANSIBLE_RUNTIME_ROOT`、`ANSIBLE_PLAYBOOK_EXECUTABLE`、`ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS`、`ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB` 和 `ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS` 装配该 runner；CPU/内存限制在支持 POSIX `setrlimit` 的本地执行环境中应用于子进程。`AutomationJobRun` 会持久化 `ansible.playbook` 的 running/completed/failed 状态、Redis message id、请求人、playbook 名称、check mode、目标数量和脱敏错误码，不保存 inventory、stdout、stderr 或 secret payload；`GET /api/v1/automation/jobs/runs` 可按当前租户只读查询这些执行状态元数据。

## Phase 5 Database Read Routing（#t53）

可配置只读副本通过 `DATABASE_READ_REPLICA_URL` 装配；未配置时 read session factory 复用 writer engine，不改变本地开发和单副本部署行为。资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、认证态用户详情 `/api/v1/auth/me`、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已通过 read database dependency 或 read session service factory 读取。当前 `GET /api/v1/audits/events`、`GET /api/v1/audits/reports/summary` 与 `GET /api/v1/audits/reports/compliance` 仍读取进程内 audit service，不接入 SQLAlchemy session；`tests/test_database_routing.py` 已把这些 audit GET 路由登记为显式 DB-free 例外，并覆盖所有 GET 路由必须被归类。登录、2FA、refresh token、MFA/密码/API key 变更、Session connection token 签发、Session 创建和关闭，以及 Workflow request 创建、提交、审批、拒绝、撤销继续使用 writer session，以保证 token 消费、状态机变更、grant 生成、审计和 session revoke 相关路径强一致。

支持的 `job_type` 白名单：

- `asset.scan`
- `credential.rotate`
- `ansible.playbook`

### POST `/api/v1/automation/jobs/asset-scans`

用途：按当前认证用户租户调度一个资产扫描后台任务，写入 `asset.scan` 队列消息。

鉴权：需要登录态；`automation:write` 或 `admin` 权限可访问。后端使用当前用户 `tenant_id` 和 `id` 写入队列，不接受前端传入 tenant 或 requested_by。

请求体：

```json
{
  "asset_id": 42,
  "scan_profile": "ssh-baseline"
}
```

响应 `202`：

```json
{
  "job_id": "1700000000000-0",
  "job_type": "asset.scan",
  "status": "queued"
}
```

安全语义：

- API payload 只包含 asset id 与 scan profile，不接受密码、token、secret、私钥或连接串。
- `AutomationJobQueue` 继续执行敏感字段名拒绝和 JSON-only 序列化约束。
- `asset.scan` worker handler 已按租户和 active asset 边界接入显式扫描执行器协议；具体网络探测实现仍需作为后续执行器切片单独验收。

### POST `/api/v1/automation/jobs/credential-rotations`

用途：按当前认证用户租户和账号可见范围调度一个凭据轮换后台任务，写入 `credential.rotate` 队列消息。

鉴权：需要登录态；`automation:write` 或 `admin` 权限可访问。后端使用当前用户 `tenant_id`、项目范围和 `id` 写入队列，不接受前端传入 tenant 或 requested_by。

请求体：

```json
{
  "account_id": 1,
  "reason": "quarterly rotation"
}
```

响应 `202`：

```json
{
  "job_id": "1700000000000-0",
  "job_type": "credential.rotate",
  "status": "queued"
}
```

安全语义：

- API 入队前必须通过当前 actor scope 确认 account 可见；跨租户或跨项目账号返回 `ACCOUNT_NOT_FOUND`。
- 队列 payload 只包含 account id 和可选 reason，不携带 secret_id、凭据明文、token、私钥或连接串。
- `credential.rotate` worker handler 会按当前租户确认 active account，创建轮换记录并调用显式改密执行器；执行成功后更新账号 `secret_id` 和轮换记录，执行失败时标记 rotation `failed` 并保留原账号 secret。

### POST `/api/v1/automation/jobs/playbooks`

用途：按当前认证用户租户调度一个 Ansible playbook 后台任务，写入 `ansible.playbook` 队列消息。

鉴权：需要登录态；`automation:write` 或 `admin` 权限可访问。后端使用当前用户 `tenant_id` 和 `id` 写入队列，不接受前端传入 tenant 或 requested_by。

请求体：

```json
{
  "playbook_name": "linux-baseline.yml",
  "target_asset_ids": [42, 43],
  "check_mode": true
}
```

响应 `202`：

```json
{
  "job_id": "1700000000000-0",
  "job_type": "ansible.playbook",
  "status": "queued"
}
```

安全语义：

- API payload 只包含 playbook 名称、目标资产 ID 列表和 check mode；额外字段 fail-closed 为请求校验错误。
- 队列 payload 不携带 extra vars、password、token、secret、私钥或连接串。
- `ansible.playbook` worker handler 已按租户确认 active 目标资产，并只向显式 runner 契约传递无凭据目标摘要；本地 `ansible-playbook` adapter 已覆盖 playbook root 路径收敛、临时 inventory 渲染、check mode 传递、不继承 secret/token 环境变量的 runtime 目录基础沙箱，以及执行超时和超时子进程回收。

### GET `/api/v1/automation/jobs/runs`

用途：按当前认证用户租户查询后台执行状态元数据，供控制台或运维侧查看 `ansible.playbook` job run 的 running/completed/failed 状态。

鉴权：需要登录态；`automation:read` 或 `admin` 权限可访问。后端只读取当前用户 `tenant_id` 下的 run，不接受前端传入 tenant。

响应 `200`：

```json
{
  "items": [
    {
      "message_id": "1700000000000-0",
      "job_type": "ansible.playbook",
      "status": "completed",
      "requested_by": "user-1",
      "playbook_name": "linux-baseline.yml",
      "check_mode": true,
      "target_count": 2,
      "error_code": null
    }
  ],
  "total": 1
}
```

安全语义：

- 响应只返回 job run 状态元数据，不返回 queue payload、inventory、stdout、stderr、secret 引用、凭据明文或下游执行输出。
- 租户隔离以当前认证用户为准，跨租户 run 不参与列表或 total。

安全语义：

- 队列消息只允许 JSON 序列化 payload，不使用 pickle 或任意 Python 对象派发。
- 未知 `job_type` fail-closed 为 `UNSUPPORTED_AUTOMATION_JOB_TYPE`。
- 未配置 handler 时 fail-closed 为 `AUTOMATION_JOB_HANDLER_NOT_CONFIGURED`，不得 ack 消息。
- 非 JSON payload format fail-closed 为 `UNSUPPORTED_AUTOMATION_JOB_PAYLOAD_FORMAT`，不得 ack 消息。
- payload 键名包含 password/token/secret/private key/connection string 等敏感字段时 fail-closed 为 `AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET`。
- `secret_id` 这类 Vault 引用可由后续执行器显式传递，但队列契约不得承载凭据明文。

## Phase 4 Audit Report API（#t49）

### GET `/api/v1/audits/reports/summary`

用途：返回当前租户审计事件的报表中心基础汇总，用于 SIEM/告警/合规报表后续页面和导出能力。

鉴权：需要登录态；`audit:read` 权限可访问。接口只读取当前用户 `tenant_id` 的审计事件，不接受前端传入 tenant。

前端：`/audits` 审计日志页读取该接口展示报表总事件、高危事件和 SIEM failed 聚合卡片；页面不展示该接口之外的原始 metadata、message、resource_id、session_id 明细。

响应 `200`：

```json
{
  "tenant_id": "tenant-a",
  "total": 2,
  "high_or_critical_total": 2,
  "by_severity": {
    "high": 1,
    "critical": 1
  },
  "by_category": {
    "session": 1,
    "connector": 1
  },
  "by_siem_delivery_status": {
    "delivered": 1,
    "failed": 1
  }
}
```

安全语义：

- 响应只返回聚合计数，不返回 audit metadata、message、resource_id、session_id 或任何可能含 token/secret/password 的明细字段。
- 租户隔离以当前认证用户为准，跨租户事件不会参与统计。
- `high_or_critical_total` 用于告警中心后续切片的高危事件入口，不等同于已实现告警投递。

## Phase 5 Audit Compliance Report API（#t54）

### GET `/api/v1/audits/reports/compliance`

用途：导出当前租户指定合规模板的审计完整性证明基础，用于 SOC2/ISO 类访问审计材料的后续文件导出与签章能力。

鉴权：需要登录态；`audit:read` 权限可访问。接口只读取当前用户 `tenant_id` 的审计事件，不接受前端传入 tenant。

查询参数：

```text
template=soc2-access
```

响应 `200`：

```json
{
  "schema_version": "janusgate.audit-compliance.v1",
  "export_format": "json",
  "content_type": "application/vnd.janusgate.audit-compliance+json;version=1",
  "download_filename": "janusgate-soc2-access-tenant-a-20260705T154000Z.json",
  "tenant_id": "tenant-a",
  "template": "soc2-access",
  "total": 2,
  "event_ids": ["audit-1", "audit-2"],
  "hash_chain_start": "sha256...",
  "hash_chain_end": "sha256...",
  "period_start": "2026-07-05T15:30:00Z",
  "period_end": "2026-07-05T15:35:00Z",
  "generated_at": "2026-07-05T15:40:00Z",
  "report_signature": "hmac-sha256...",
  "report_signature_algorithm": "hmac-sha256",
  "report_signature_key_id": "local-secret-key"
}
```

安全语义：

- 响应只返回事件 ID、hash chain 起止、报告期间、模板、正式 JSON 导出格式元数据、报表签名、签名算法和签名 key id。
- 响应不返回 audit metadata、message、resource_id、session_id、SIEM 下游错误或任何可能含 token/secret/password 的明细字段。
- 租户隔离以当前认证用户为准，跨租户事件不会进入 `event_ids`、hash chain 起止或 total。
- 前端 `/audits` 使用该接口生成 SOC2 合规报表 JSON 下载文件，并使用 `download_filename` 与 `content_type` 作为下载文件名和 Blob media type；页面只展示事件数量与报表签名摘要，不展示原始审计明细。

## Phase 4 Approval Policy API（#t48）

### POST `/api/v1/workflows/approval-policies`

用途：在当前租户内创建一条 JIT approval policy template，用于审批策略 DSL、版本管理和策略模拟接入。

鉴权：需要登录态；`admin` 或 `workflow:admin` 权限可访问。后端使用当前用户 `tenant_id` 写入，不接受前端传入 tenant。

请求体：

```json
{
  "resource_selector": {
    "asset_id": "asset-1",
    "protocol": "ssh"
  },
  "action_selector": "session.connect",
  "approver_subject_ids": ["manager-1"],
  "approver_mode": "named_user",
  "require_mfa_for_requester": true,
  "require_mfa_for_approver": true,
  "max_grant_ttl_seconds": 900,
  "allow_self_approval": false,
  "risk_level": "high",
  "rollout_percentage": 100,
  "dsl_conditions": {
    "context_equals": {
      "protocol": "ssh"
    },
    "context_in": {
      "account_tier": ["production", "break-glass"]
    },
    "context_number_gte": {
      "risk_score": 80
    },
    "context_number_lte": {
      "session_duration_minutes": 60
    },
    "context_not_equals": {
      "maintenance_window": "true"
    },
    "context_not_in": {
      "account_tier": ["sandbox", "break-glass"]
    },
    "context_exists": ["ticket_id", "risk_score"],
    "any": [
      {
        "all": [
          {"context_equals": {"protocol": "ssh"}},
          {"context_in": {"risk_level": ["high", "critical"]}}
        ]
      },
      {"context_equals": {"break_glass": "true"}}
    ]
  }
}
```

响应 `201`：

```json
{
  "id": "ap_...",
  "tenant_id": "tenant-a",
  "resource_selector": {
    "asset_id": "asset-1",
    "protocol": "ssh"
  },
  "action_selector": "session.connect",
  "approver_subject_ids": ["manager-1"],
  "approver_mode": "named_user",
  "require_mfa_for_requester": true,
  "require_mfa_for_approver": true,
  "max_grant_ttl_seconds": 900,
  "allow_self_approval": false,
  "risk_level": "high",
  "rollout_percentage": 100,
  "created_at": "2026-07-04T06:40:00Z",
  "updated_at": "2026-07-04T06:40:00Z"
}
```

安全语义：

- 策略模板只能写入当前租户；跨租户读取不会返回该策略。
- `PolicyDecisionService` 可接收已加载的 approval policy template；匹配当前租户、action selector、resource selector 且落入 deterministic rollout bucket 的请求会要求 JIT approval，并在 `APPROVAL_REQUIRED` obligations 中返回 approval policy、审批人、MFA、TTL 和风险级别元数据。
- `rollout_percentage` 默认为 `100`，取值范围 `0-100`；`0` 表示当前 active policy 不命中任何 subject/resource，`100` 表示全部命中，`1-99` 使用策略 ID、租户、subject ID 和 resource ID 做稳定哈希分桶。灰度排除时响应保持 deny-by-default 的 `NO_MATCHING_POLICY`，不会返回 secret 或跨租户信息。
- `dsl_conditions.context_equals` 支持按请求 `context` 中的键值做精确匹配；`dsl_conditions.context_in` 支持按请求 `context` 中的键值做枚举匹配，枚举值必须是数组；`dsl_conditions.context_number_gte` / `context_number_lte` 支持按请求 `context` 中的数值做大于等于 / 小于等于阈值匹配，数值可为 JSON number 或可解析数字字符串，缺失、布尔值、非数字、NaN 或 Infinity 均 fail-closed 为不命中；`dsl_conditions.context_not_equals` 支持按请求 `context` 中的键值做排除匹配，值相等时该策略不命中；`dsl_conditions.context_not_in` 支持按请求 `context` 中的键值做枚举排除匹配，枚举值必须是数组，命中枚举值时该策略不命中；`dsl_conditions.context_exists` 支持要求请求 `context` 携带指定字段，字段列表必须是非空字符串数组，缺失或值为 `null` 时不命中。`dsl_conditions.all` 和 `dsl_conditions.any` 支持非空数组形式的递归组合表达式，同一对象内多个条件按 AND 语义同时满足。不匹配、DSL JSON 损坏、`context_in` / `context_not_in` 非数组、数值阈值非对象、`context_exists` 非数组或空数组、`all` / `any` 非数组或空数组、`context_not_equals` 非对象或出现未支持操作符时 fail-closed 为 `NO_MATCHING_POLICY`。
- Phase 4 #t48 版本管理基础中，响应会返回 `policy_family_id`、`version` 与 `is_active`；新建策略默认为同 family 的 v1 active 版本。
- 响应不返回 DSL 条件、凭据、连接 token、审批下游密钥或外部通知 secret。

### GET `/api/v1/workflows/approval-policies`

用途：返回当前租户可见的 approval policy template 列表。

鉴权：需要登录态；`admin` 或 `workflow:admin` 权限可访问。响应按 repository 当前顺序返回 `{items,total}`。

版本语义：只返回当前租户 active/latest approval policy version；历史版本不会参与默认列表或策略模拟。

### POST `/api/v1/workflows/approval-policies/{policy_id}/versions`

用途：基于当前租户内已有 approval policy 创建同一 policy family 的新版本，用于后续灰度、回滚和 DSL 执行前的版本化基础。

鉴权：需要登录态；`admin` 或 `workflow:admin` 权限可访问。`policy_id` 必须属于当前租户，否则返回 `404` / `APPROVAL_POLICY_NOT_FOUND`。

请求体与 `POST /api/v1/workflows/approval-policies` 相同，包含可选 `rollout_percentage`。创建成功后，新版本 `version` 按 family 递增并成为 `is_active=true`，同 family 旧 active 版本会被停用；默认列表和模拟只使用最新 active 版本。

安全语义：

- 新版本创建只在当前租户 policy family 内生效；不能借此探测或覆盖跨租户策略。
- 响应仍只返回 selector、审批人、MFA、TTL、风险级别和版本元数据，不返回 DSL、凭据、连接 token、Webhook secret 或任何下游密钥。
- 版本可携带新的 `dsl_conditions.context_equals` / `context_in` / `context_number_gte` / `context_number_lte` / `context_not_equals` / `context_not_in` / `context_exists` 条件，也可携带 `all` / `any` 组合表达式；响应仍不回显 DSL 条件。

### POST `/api/v1/workflows/approval-policies/{policy_id}/rollback`

用途：把当前租户内同一 approval policy family 显式回滚到指定版本。`policy_id` 可以是当前 active 版本或历史 inactive 版本。

鉴权：需要登录态；`admin` 或 `workflow:admin` 权限可访问。`policy_id` 必须属于当前租户，否则返回 `404` / `APPROVAL_POLICY_NOT_FOUND`。

响应 `200`：返回被激活的 approval policy version。回滚成功后，同租户同 family 只有该版本 `is_active=true`，默认列表和模拟只读取回滚后的 active 版本。

安全语义：

- 回滚只在当前租户 policy family 内生效；不能借此探测或覆盖跨租户策略。
- 响应仍只返回 selector、审批人、MFA、TTL、风险级别和版本元数据，不返回 DSL、凭据、连接 token、Webhook secret 或任何下游密钥。
- 回滚会恢复目标版本保存的 `dsl_conditions.context_equals` / `context_in` / `context_number_gte` / `context_number_lte` / `context_not_equals` / `context_not_in` / `context_exists` 条件以及 `all` / `any` 组合表达式；响应仍不回显 DSL 条件。

### POST `/api/v1/workflows/approval-policies/simulate`

用途：在当前租户内使用已保存的 approval policy templates 运行一次策略模拟，返回与 `PolicyDecisionService` 一致的 `decision`、`reason_code`、`explain_trace`、`obligations` 和 `ttl_seconds`，用于后续策略 DSL、版本管理和灰度发布前的预检。

鉴权：需要登录态；`admin` 或 `workflow:admin` 权限可访问。后端只读取当前用户 `tenant_id` 下的策略模板，并把模拟请求的 subject/resource 租户锁定到当前租户，不接受前端传入 tenant 作为策略选择依据。

请求体：

```json
{
  "subject": {
    "id": "user-2",
    "type": "user",
    "tenant_id": "tenant-a"
  },
  "action": "session.connect",
  "resource": {
    "id": "asset-1",
    "type": "asset",
    "tenant_id": "tenant-a"
  },
  "context": {
    "protocol": "ssh",
    "account_id": "account-1"
  },
  "connector_trusted": true,
  "mfa_verified": false
}
```

响应 `200`：

```json
{
  "decision": "deny",
  "reason_code": "APPROVAL_REQUIRED",
  "explain_trace": [
    "subject=user:user-2",
    "action=session.connect",
    "resource=asset:asset-1",
    "approval_policy:ap_...:matched",
    "approval_required_but_missing"
  ],
  "obligations": {
    "workflow_required": true,
    "approval_policy_id": "ap_...",
    "approver_subject_ids": ["manager-1"],
    "risk_level": "high"
  },
  "ttl_seconds": 0,
  "audit_event_id": "audit_..."
}
```

安全语义：

- 模拟结果只基于当前租户策略；跨租户策略不会被读取或命中。
- 响应只返回决策解释和 obligations，不返回凭据、连接 token、Webhook secret 或任何下游密钥。
- 当前切片模拟已保存策略模板、`context_equals` / `context_in` / `context_number_gte` / `context_number_lte` / `context_not_equals` / `context_not_in` / `context_exists` DSL 条件、`all` / `any` 组合表达式和 rollout 分桶；更多 DSL 操作符仍待后续切片。

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
- `NotificationDeliveryWorker` 只读取 `pending` / 到期 `failed` 记录，成功后标记 `delivered`，失败后更新 `attempts`、`last_error` 与下一次重试时间，达到最大尝试次数后标记 `dead_letter`。
- `HttpWebhookNotificationSender` 使用 `POST` 向 endpoint URL 投递 `{event_type, delivery_id, payload}`，并附带 `X-JanusGate-Event-Type` 与 `X-JanusGate-Tenant-Id`。非 2xx 或网络错误会 fail-closed 抛出稳定错误，worker 随后进入重试/死信流程；错误信息不包含 payload、signing secret 或下游响应体。
- 当前切片不内置 IM sender 或多级审批。

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

## Phase 4 Observability Metrics（#t51）

### GET `/metrics`

用途：以 Prometheus 文本格式暴露后端 HTTP 请求指标，供 Prometheus 或兼容采集器抓取。

鉴权：当前 foundation 不接入业务登录态；生产暴露应由部署层、Ingress 或采集网络控制访问范围。

响应 `200`，`Content-Type: text/plain; version=0.0.4; charset=utf-8`：

```text
# HELP janusgate_http_requests_total Total HTTP requests handled by JanusGate.
# TYPE janusgate_http_requests_total counter
janusgate_http_requests_total{method="GET",path="/health",status_code="200"} 1
# HELP janusgate_http_request_duration_seconds HTTP request duration in seconds.
# TYPE janusgate_http_request_duration_seconds histogram
janusgate_http_request_duration_seconds_bucket{method="GET",path="/health",status_code="200",le="0.005"} 1
```

安全语义：

- 指标标签只包含 HTTP method、路由模板 path 和 status_code。
- 不记录请求体、响应体、Authorization header、token、secret、连接串或用户输入。
- `/metrics` 自身不会计入 HTTP 请求指标，避免采集周期污染业务请求统计。

## Phase 4 Vault KMS Adapter（#t50）

当前 #t50 已提供 envelope encryption provider 与本地 AES-GCM KMS adapter。`EnvelopeEncryptedSecretProvider` 每条 secret 生成独立 32 字节 DEK，secret ciphertext 使用 secret id 作为 AAD；DEK 只以 KMS-wrapped 形式随 record 保存，不保存凭据明文。

配置：

- `VAULT_LOCAL_KMS_MASTER_KEY`：可选，本地 KMS adapter 的 base64 编码 32 字节 master key。未配置时不会自动启用本地 adapter。

安全语义：

- `LocalKmsEnvelopeKeyProvider` 只接受 32 字节 master key；长度不匹配 fail-closed 为 `KMS_MASTER_KEY_MUST_BE_32_BYTES`。
- wrapped DEK 使用带版本前缀的 AES-GCM 格式保存，不等于 DEK 明文或 hex 表示。
- 错误 master key、损坏 wrapped key 或不支持格式均 fail-closed 为 `KMS_UNWRAP_DENIED`。
- 真实云 KMS/HSM/Vault adapter、审批后 unwrap 与 break-glass 流程仍是后续切片；新增 API 不得返回 wrapped DEK、plaintext DEK 或凭据明文。

## Phase 5 Audit Compliance Report（#t54）

### GET `/api/v1/audits/reports/compliance`

用途：按当前登录用户租户导出指定模板的合规报表摘要，供控制台下载和审计留存。

响应包含当前租户事件 ID 列表、hash chain 起止、报告期间、`report_signature`、签名算法/key id、正式 JSON 导出格式元数据，以及本轮 append-only WORM 归档元数据：

- `schema_version`：当前为 `janusgate.audit-compliance.v1`，用于审计留存和后续兼容解析。
- `export_format`：当前为 `json`。
- `content_type`：当前为 `application/vnd.janusgate.audit-compliance+json;version=1`。
- `download_filename`：后端生成的安全文件名，格式为 `janusgate-<template>-<tenant>-<utc>.json`，只包含文件名安全字符。
- `report_signature_algorithm`：默认本地实现为 `hmac-sha256`；配置 `COMPLIANCE_REPORT_SIGNER_PROVIDER=external-hmac` 后使用 `external-hmac-sha256`，后续真实 KMS/签章服务仍复用该字段标识算法。
- `report_signature_key_id`：默认本地实现为 `local-secret-key`；外部 HMAC adapter 使用 `COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID` 作为响应 key id，不回显真实 signing secret。
- `worm_storage_status`：当前为 `recorded`，表示本次导出已写入归档记录。
- `worm_record_id`：本次归档记录 ID。
- `worm_sequence_number`：进程内归档序号，单调递增。
- `worm_content_hash`：基于租户、模板、事件 ID 和 hash chain 等无敏感报表摘要计算的稳定 SHA-256。

安全语义：

- 接口复用 `audit:read` 权限，并只读取当前用户 `tenant_id` 下的事件。
- 响应不返回原始 `metadata`、`message`、`resource_id`、`session_id`、凭据明文、token 或连接串。
- 当前 WORM 归档为后端 repository 内 append-only 基础；签章支持本地 HMAC 与配置驱动 external HMAC adapter，后者缺少外部 key id 或 signing secret 时 fail-closed；真实外部 WORM 存储和云 KMS/证书签章服务仍是后续 Phase 5 切片。

## 前端解析建议

1. 先读 `code` 做流程判断；业务码优先于 HTTP 状态。
2. 展示文案优先 `message`，必要时展开 `detail`。
3. 422 表单错误按 `detail[].loc` 映射字段。
4. 不要依赖未声明字段；新增字段必须先进入 OpenAPI 和本文件。
5. `/accounts` 控制台页只消费 `secret_id` 引用和 rotation 状态，不展示或缓存凭据明文；调度轮换时调用 `POST /api/v1/accounts/{account_id}/rotations` 并使用固定业务原因或后续表单输入。
