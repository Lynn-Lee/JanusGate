# JanusGate

> 策略驱动的 PAM / 零信任访问网关

JanusGate 是企业级特权访问管理（PAM）平台，基于 JumpServer 业务功能参考，全新架构重写。提供统一、安全、可审计的 SSH、RDP、Kubernetes、数据库和远程应用访问入口。

## 架构

```
Interface    REST / WebSocket / Connector API
Services     Auth / Policy / Inventory / Session / Audit / Vault
Domain       Identity / Asset / Credential / Policy / Session / AuditEvent
Infra        PostgreSQL / Redis / Object Storage / KMS
```

## 快速启动

```bash
cp .env.example .env
# 编辑 .env 设置 SECRET_KEY（必须不少于 32 字符）
docker compose up -d
```

## 文档

- [最终评估报告](docs/architecture/00-final-evaluation.md) — 基于 JumpServer 的完整评估与重构基线
- [主基线文档与研发总计划](docs/architecture/10-master-evaluation-and-roadmap.md) — 当前唯一权威 roadmap
- [Phase 3 API 契约](docs/api-contract.md) — 前后端联调契约与错误码规范

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12+ / FastAPI / SQLAlchemy 2.0 async |
| 数据库 | PostgreSQL 16 |
| 缓存/队列 | Redis |
| 加密 | AES-256-GCM / bcrypt / JWT (HS256) |
| 部署 | Docker Compose / Kubernetes + Helm |

## License

待定

## Phase 3 前端控制台

Phase 3 MVP 前端位于 `frontend/`，采用 React + TypeScript + Vite + Ant Design。当前范围按 `docs/architecture/08-phase3-mvp-prd-ia.md` 锁定为 6 个页面：登录、资产、会话、Workflow/JIT、审计日志、系统设置。

```bash
npm --prefix frontend install
npm --prefix frontend run dev
npm --prefix frontend run test
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
```

开发服务默认代理 `/api` 和 `/health` 到 `http://127.0.0.1:8000`；也可通过 `VITE_API_BASE_URL` 指向独立后端。会话页通过 `GET /api/v1/sessions/` 读取后端记录的当前用户会话，Workflow/JIT 页创建会话前会先换取真实短期 `connection_token`。

Phase 3 API-level 主链路 smoke 可在后端运行：

```bash
cd backend
pytest -q tests/test_phase3_api_smoke.py
```

Phase 3 QA Go/No-Go 证据包位于 `docs/qa/phase3-go-no-go.md`，覆盖 6 页面验收矩阵、主链路 smoke、安全验收、coverage gate、Compose `/health` smoke 与 Helm render smoke。

CI 已包含 Phase 3 部署 smoke 门禁：后端 `pytest --cov=app --cov-report=term-missing --cov-fail-under=80` 执行覆盖率门禁，`docker compose config` 校验 Compose 配置可渲染，`scripts/phase3-compose-health-smoke.sh` 启动 backend 及其依赖并请求 `http://localhost:8000/health`，`helm lint` 与 `helm template` 校验 Helm chart 可 lint/render。Compose health smoke 脚本退出时会清理本轮容器和卷。

## Phase 4 多租户基座

Phase 4 #t42 已启动后端 tenancy 基座：`User` 持久化 `tenant_id` 以及可选 `organization_id`、`team_id`、`project_id`，并新增 `Organization`、`Team`、`Project` 模型。后端查询可通过 `app.tenancy.scope.scoped_select()` 默认注入 `tenant_id` 过滤，避免 Phase 4 后续权限与 UI 接入时绕过租户隔离。当前已提供租户隔离的 Organization 管理 API：`GET /api/v1/tenancy/organizations` 与 `POST /api/v1/tenancy/organizations`；Team 管理 API：`GET /api/v1/tenancy/teams` 与 `POST /api/v1/tenancy/teams`；以及 Project 管理 API：`GET /api/v1/tenancy/projects` 与 `POST /api/v1/tenancy/projects`。前端控制台已新增 `/tenancy` 只读组织结构页，展示当前用户可见的 Organization、Team、Project。PolicyRule 现在可按 `organization_ids`、`team_ids`、`project_ids` 绑定资源维度，资源缺失或不匹配绑定维度时 fail-closed 为无匹配策略。

Phase 4 #t43 已启动资产账号托管后端切片：新增 `Account` 持久化模型与 `GET/POST /api/v1/accounts/`，账号记录绑定当前租户、资产、Vault `secret_id` 以及可选 Organization/Team/Project 维度。账号列表复用 `scoped_select()` 做租户和项目维度收敛，响应只返回 secret 引用，不返回凭据明文。当前已补 `CredentialRotation` 调度记录与 `GET/POST /api/v1/accounts/{account_id}/rotations`，按账号可见范围做租户/项目维度收敛；并新增 `CredentialRotationWorker` 执行到期调度，成功后更新账号 `secret_id`，记录旧/新 secret 引用并支持 completed rotation 回滚，失败时标记 rotation `failed` 且保留原账号 secret。前端控制台已新增 `/accounts` 账号托管页，可查看账号 secret 引用、轮换记录并调度凭据轮换，不展示凭据明文。

Phase 4 #t44 已启动 SSH CA / 临时证书能力：新增 `SshCertificateAuthority` 与 `SshCertificate` 持久化模型，`Asset` 可绑定信任的 SSH CA，`SshCertificateService` 负责按租户、资产信任配置与 SSH account 边界签发短期证书，并支持同租户 issued certificate 撤销。当前已暴露 `GET/POST /api/v1/ssh-certificate-authorities/`、`GET /api/v1/ssh-certificate-authorities/trust-bundle`、`POST /api/v1/ssh-certificate-authorities/{authority_id}/disable`、`GET/POST /api/v1/ssh-certificates/` 与 `POST /api/v1/ssh-certificates/{certificate_id}/revoke`，按当前用户租户和账号可见范围收敛，响应不返回 CA 私钥材料或 CA 私钥 secret 引用。后端 API 已接入 Vault-backed `VaultOpenSshCertificateSigner`，可通过 CA 私钥 secret 引用生成可解析的 OpenSSH user certificate；连接器可读取当前租户 active 资产实际信任的 CA 公钥 bundle，前端控制台已新增 `/ssh-ca` 入口，可查看 CA、trust bundle 和临时证书，并撤销 issued 证书。

Phase 4 #t45 已启动连接器/边缘网关生产级信任链：Connector Registry 现在记录连接器 `last_heartbeat_at`，注册成功会建立初始心跳租约，后续 `record_heartbeat()` 刷新租约；签发 connection token 前会 fail-closed 检查 active 状态与 heartbeat TTL，过期连接器返回 `CONNECTOR_HEARTBEAT_EXPIRED`。Enrollment token 也可绑定连接器 mTLS 证书指纹，签发 token 时若 presented certificate fingerprint 不匹配会返回 `CONNECTOR_MTLS_CERTIFICATE_MISMATCH`；也可绑定 attestation nonce/digest，注册请求缺失或不匹配 attestation 会 fail-closed 拒绝。Connector Registry 已支持 active connector public key rotation，并记录 previous/current fingerprint 与轮换时间；inactive 或 revoked connector 轮换 fail-closed。当前已补持久化 Connector 管理 API：`GET/POST /api/v1/connectors/`、`POST /api/v1/connectors/{connector_id}/heartbeat` 与 `POST /api/v1/connectors/{connector_id}/rotate-key`，按当前租户收敛，响应只暴露运行态、能力声明和绑定布尔值，不返回 enrollment token、attestation digest 或私钥材料。后端包内已提供轻量 `ConnectorSdkClient`，覆盖创建 connector、heartbeat 与 key rotation，并把统一错误响应映射为不泄露 access token 的 SDK 异常。

Phase 4 #t46 已启动会话录制与命令检索后端基础：新增租户隔离的 `SessionRecording` 与 `SessionCommandEvent` 持久化模型，并提供 `POST /api/v1/sessions/{session_id}/recordings`、`POST /api/v1/session-recordings/{recording_id}/commands`、`POST /api/v1/connectors/{connector_id}/session-recordings/{recording_id}/commands`、`GET /api/v1/session-recordings/{recording_id}/commands`、`POST /api/v1/session-recordings/{recording_id}/close` 与 `GET /api/v1/session-recordings/commands?query=...`。命令事件、连接器上报、录制命令时间线和录制关闭按当前租户收敛，inactive 或跨租户 connector fail-closed，跨租户或已关闭录制返回 `SESSION_RECORDING_NOT_FOUND`，命令输出摘要会脱敏 token/password/secret/credential 赋值文本；命令检索在 PostgreSQL 上使用 `to_tsvector` / `plainto_tsquery` 与 GIN 索引，在 SQLite 测试环境保留 `ILIKE` fallback；前端 `/sessions` 已提供按 Recording ID 加载的只读回放命令时间线入口。

Phase 4 #t47 已启动 WebHook / 通知中心后端基础：新增租户隔离的 `WebhookEndpoint` 持久化模型与 `GET/POST /api/v1/webhook-endpoints/`。接口按当前用户租户收敛 endpoint 列表和创建行为，响应只暴露 webhook 名称、URL、事件类型、状态以及 signing secret 是否已配置，不返回 signing secret 明文或摘要。当前已补 `NotificationRule` 持久化模型与 `GET/POST /api/v1/notification-rules/`，规则必须引用当前租户 active WebHook endpoint；并新增 `NotificationDelivery` 队列记录与 `POST /api/v1/notification-rules/{rule_id}/deliveries`、`GET /api/v1/notification-deliveries/`。`NotificationDeliveryWorker` 已提供到期投递、失败重试、最大次数后 dead-letter 的服务契约，并可通过 `HttpWebhookNotificationSender` 向 HTTPS WebHook endpoint 投递已脱敏 payload，非 2xx 或网络错误会 fail-closed 进入重试/死信状态，不向响应或错误泄露 payload、secret 或下游响应体。IM sender 和多级审批仍是后续切片。

Phase 4 #t48 已启动 JIT 策略模板 / 审批策略 DSL 后端基础：现有 `ApprovalPolicyModel` 已通过 `GET/POST /api/v1/workflows/approval-policies` 暴露租户隔离的策略模板管理 API，创建与列表均要求 `workflow:admin` 或 `admin` 权限。接口使用当前用户 `tenant_id` 写入和读取，不接受前端传入 tenant；响应只返回资源 selector、action selector、审批人、MFA、TTL、风险级别与灰度百分比元数据。`PolicyDecisionService` 已可接收 approval policy template，将匹配 selector 且落入 deterministic rollout bucket 的 session 请求 fail-closed 转为 `APPROVAL_REQUIRED` 并返回审批策略 obligations；`POST /api/v1/workflows/approval-policies/simulate` 可在当前租户内复用同一决策引擎做策略模拟，不接受跨租户策略探测。当前已补 approval policy family/version 基础：`POST /api/v1/workflows/approval-policies/{policy_id}/versions` 可在当前租户内创建递增版本并停用旧 active 版本，列表与模拟默认只读取 active/latest 版本；`POST /api/v1/workflows/approval-policies/{policy_id}/rollback` 可把同租户同 family 显式回滚到指定版本；策略支持 `rollout_percentage` 做 0-100 deterministic 灰度命中，并已支持 DSL `context_equals` 精确匹配、`context_in` 枚举匹配、`context_not_equals` 排除匹配和 `context_not_in` 枚举排除匹配，context 不匹配时 fail-closed 为 `NO_MATCHING_POLICY` 且响应不回显 DSL 条件。复杂表达式与更多 DSL 操作符仍是后续切片。

Phase 4 #t49 已启动 SIEM / 告警 / 报表中心基础：`GET /api/v1/audits/reports/summary` 返回当前租户审计事件 total、severity、category、SIEM delivery 状态和高危计数聚合。接口复用 `audit:read` 权限，按当前用户 `tenant_id` 收敛，不返回 metadata、message、resource_id、session_id 或任何凭据相关明细字段。前端 `/audits` 审计页已展示报表总事件、高危事件和 SIEM failed 聚合卡片，不展示原始审计 metadata。

Phase 4 #t50 已启动 Vault 生产级后端基础：`app.vault.provider` 现在提供 `KmsKeyProvider` 协议与 `EnvelopeEncryptedSecretProvider`，每条 secret 使用随机 32 字节 data key 加密，再通过 KMS provider 包装 data key 保存；解密时必须成功 unwrap data key，KMS 拒绝时 fail-closed。当前切片仍为内存 provider foundation，不包含真实云 KMS/HSM/Vault adapter、审批后 unwrap 或 break-glass 流程。

Phase 4 #t51 已启动可观测性基础：后端提供 Prometheus 文本格式 `GET /metrics`，并通过 HTTP middleware 记录请求总数与延迟 histogram，指标标签仅包含 method、路由模板 path 和 status_code，不写入 token、secret、连接串或请求/响应正文。OpenTelemetry 分布式追踪、Loki 日志管道和更完整的部署暴露策略仍是后续切片。

Phase 4 #t52 已启动 Automation Worker 队列基础：`AutomationJobQueue` 使用 Redis Streams 风格 `xadd` 写入 JSON-only 后台任务消息，当前白名单任务类型为 `asset.scan`、`credential.rotate` 和 `ansible.playbook`。队列契约拒绝未知任务类型和 password/token/secret/private key 等敏感 payload 字段，不使用 pickle 或任意 Python 对象派发。真实 worker 消费循环、Ansible/扫描/改密执行器和调度 API 仍是后续切片。
