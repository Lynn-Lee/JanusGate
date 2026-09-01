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

CI 已包含 Phase 3 部署 smoke 门禁：后端 `pytest --cov=app --cov-report=term-missing --cov-fail-under=80` 执行覆盖率门禁，`docker compose config` 校验 Compose 配置可渲染，`scripts/phase3-compose-health-smoke.sh` 启动 backend 及其依赖并请求 `http://localhost:8000/health`，`helm lint` 与 `helm template` 校验 Helm chart 可 lint/render。Compose health smoke 脚本退出时会清理本轮容器和卷。Phase 5 #t59 文档站静态发布包可通过 `scripts/build-docs-site.sh dist/docs-site` 生成，当前包含安装手册、管理员手册、管理员脱敏截图证据资产、截图测试数据夹具、真实截图归档合约、API 文档、操作 runbook、操作 runbook evidence manifest、license operations evidence manifest、OpenAPI JSON 和 manifest；截图证据已扩展覆盖 License / Edition、审计报表、会话回放、Tenancy、账号轮换和 SSH CA；`scripts/phase5-docs-browser-screenshots-smoke.sh` 默认校验截图证据 wiring，显式设置 `JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1` 后可在具备 Playwright 的前端环境按 fixture 注入脱敏 API 响应、执行页面操作、校验 must_show/must_not_show 合约并重抓真实浏览器截图到 `docs/site/assets/screenshots/live-screenshots/*.png`；`docs/site/fixtures/admin-screenshot-archive.json` 固定真实运行环境截图归档时的 route、静态 SVG artifact、live PNG artifact、回归来源和捕获入口。

Phase 5 高可用配置 smoke 可运行 `scripts/phase5-ha-config-smoke.sh`，覆盖 Docker Compose read-replica 环境渲染、Helm HPA 在 memory token store 下 fail-closed，以及 Redis-backed connection token store + read-replica Secret 的多副本渲染路径；该脚本已接入 CI 的 Helm 门禁。真实 Kubernetes 多副本 smoke 脚本位于 `scripts/phase5-k8s-multi-replica-smoke.sh`，需要当前 kube context 可创建 namespace，并通过环境变量提供真实 PostgreSQL writer、read replica、Redis 和 SECRET_KEY；CI 中仅在显式设置 repository variable `JANUSGATE_RUN_K8S_SMOKE=1` 且配置对应 secrets 时执行。Phase 5 #t55 容量模型 smoke 可运行 `scripts/phase5-capacity-model-smoke.sh`，使用 `JANUSGATE_LOAD_TEST_RPS`、`JANUSGATE_LOAD_TEST_P95_MS`、`JANUSGATE_LOAD_TEST_ERROR_RATE_PERCENT` 和 `JANUSGATE_CAPACITY_MODEL_MAX_CORE_RPS` 校验 p95、错误率和 `capacity_headroom_percent` SLO；基线说明见 `docs/performance/phase5-capacity-model.md`。当前已补 `scripts/phase5-core-api-load-test.sh`，可对真实 dev/staging/production-like 环境执行认证 GET endpoint mix，并把聚合结果喂给容量模型 smoke；`docs/performance/phase5-load-test-evidence-template.json` 固定真实环境压测证据包的目标环境、runner 配置、容量模型结果和 artifact manifest，`scripts/phase5-load-test-evidence-smoke.sh` 会校验归档合约及敏感字段名边界。Phase 5 #t56 supply-chain security foundation 已接入 CI：`scripts/phase5-supply-chain-security-smoke.sh` 校验 GitHub Actions 中的 SBOM、Cosign 签名和 Trivy 高危/严重漏洞扫描门禁 wiring；CI 会对仓库文件系统运行 Trivy vulnerability gate，release tag 镜像推送后会为 digest 生成 SPDX JSON SBOM，并使用 GitHub OIDC + Cosign keyless signing 对镜像 digest 签名。Phase 5 #t56 runtime monitoring smoke foundation 已接入 CI：`scripts/phase5-runtime-monitoring-smoke.sh` 校验 Compose/Helm 运行时加固、Prometheus `/metrics` 回归契约和 `deploy/monitoring/phase5-runtime-alerts.yaml` 告警规则基线；当前告警覆盖高 5xx、p95 延迟和 metrics 缺失；真实 Alertmanager 接入和目标环境告警演练仍待后续切片。Phase 5 #t57 release readiness foundation 已接入 CI：`scripts/phase5-release-readiness-smoke.sh` 校验后端、健康检查和 Helm 语义版本一致性、release tag pipeline wiring、Alembic 迁移检查点以及 Helm 回滚 runbook 文档；真实生产发布演练与迁移回滚演练仍待后续切片。Phase 5 #t58 Edition / License 边界 foundation 已启动：`GET /api/v1/admin/license-summary` 仅允许 `admin` 读取当前 configured/effective edition、license 状态和 feature flag 摘要，默认 community；enterprise 配置必须提供验签通过且未过期的 license，否则 fail-closed 回退 community。当前支持 HMAC-SHA256、离线 Ed25519 公钥验签和 `external-http` 外部商业授权服务验证 foundation；外部验证只向 HTTPS validation endpoint 发送 opaque license key，服务端 bearer token 从环境注入，不写入 DB。`POST /api/v1/admin/license-config` 已提供 admin-only 持久化配置 foundation，写入后 summary 优先读取 DB 中的激活配置，无记录时回退环境变量。响应不泄露 license key、签名 secret、公钥、validation token 或原始 payload。前端 `/settings` 已接入 License / Edition 摘要和最小 License lifecycle 激活表单，可选择 `hmac`、`ed25519` 或 `external-http` verifier；`external-http` 只提交 opaque license key，validation endpoint 与 service token 仍从环境读取，不写入 DB 或回显。页面展示提交后的 configured/effective edition、license status、启用与禁用能力，不回显 license key、签名 secret、公钥或原始 payload。每次成功写入 license 配置会追加 `admin.license_config.updated` 审计事件，只记录版本、状态、verifier、密钥材料是否已配置和启用 feature 摘要，不保存 license key、签名 secret、公钥、validation token 或原始 payload。`docs/site/fixtures/license-operations-evidence.json` 已固定外部授权服务 SLA、fail-closed drill、timeout budget、key custody owner、rotation cadence 和 revocation process 证据字段，明确不得归档 license key、signing secret、validation token、私钥或原始授权 payload。

## Phase 4 多租户基座

Phase 4 #t42 已启动后端 tenancy 基座：`User` 持久化 `tenant_id` 以及可选 `organization_id`、`team_id`、`project_id`，并新增 `Organization`、`Team`、`Project` 模型。后端查询可通过 `app.tenancy.scope.scoped_select()` 默认注入 `tenant_id` 过滤，避免 Phase 4 后续权限与 UI 接入时绕过租户隔离。当前已提供租户隔离的 Organization 管理 API：`GET /api/v1/tenancy/organizations` 与 `POST /api/v1/tenancy/organizations`；Team 管理 API：`GET /api/v1/tenancy/teams` 与 `POST /api/v1/tenancy/teams`；以及 Project 管理 API：`GET /api/v1/tenancy/projects` 与 `POST /api/v1/tenancy/projects`。前端控制台已新增 `/tenancy` 只读组织结构页，展示当前用户可见的 Organization、Team、Project。PolicyRule 现在可按 `organization_ids`、`team_ids`、`project_ids` 绑定资源维度，资源缺失或不匹配绑定维度时 fail-closed 为无匹配策略。

Phase 4 #t43 已启动资产账号托管后端切片：新增 `Account` 持久化模型与 `GET/POST /api/v1/accounts/`，账号记录绑定当前租户、资产、Vault `secret_id` 以及可选 Organization/Team/Project 维度。账号列表复用 `scoped_select()` 做租户和项目维度收敛，响应只返回 secret 引用，不返回凭据明文。当前已补 `CredentialRotation` 调度记录与 `GET/POST /api/v1/accounts/{account_id}/rotations`，按账号可见范围做租户/项目维度收敛；并新增 `CredentialRotationWorker` 执行到期调度，成功后更新账号 `secret_id`，记录旧/新 secret 引用并支持 completed rotation 回滚，失败时标记 rotation `failed` 且保留原账号 secret。前端控制台已新增 `/accounts` 账号托管页，可查看账号 secret 引用、轮换记录并调度凭据轮换，不展示凭据明文。

Phase 4 #t44 已启动 SSH CA / 临时证书能力：新增 `SshCertificateAuthority` 与 `SshCertificate` 持久化模型，`Asset` 可绑定信任的 SSH CA，`SshCertificateService` 负责按租户、资产信任配置与 SSH account 边界签发短期证书，并支持同租户 issued certificate 撤销。当前已暴露 `GET/POST /api/v1/ssh-certificate-authorities/`、`GET /api/v1/ssh-certificate-authorities/trust-bundle`、`POST /api/v1/ssh-certificate-authorities/{authority_id}/disable`、`GET/POST /api/v1/ssh-certificates/` 与 `POST /api/v1/ssh-certificates/{certificate_id}/revoke`，按当前用户租户和账号可见范围收敛，响应不返回 CA 私钥材料或 CA 私钥 secret 引用。后端 API 已接入 Vault-backed `VaultOpenSshCertificateSigner`，可通过 CA 私钥 secret 引用生成可解析的 OpenSSH user certificate；连接器可读取当前租户 active 资产实际信任的 CA 公钥 bundle，前端控制台已新增 `/ssh-ca` 入口，可查看 CA、trust bundle 和临时证书，并撤销 issued 证书。

Phase 4 #t45 已启动连接器/边缘网关生产级信任链：Connector Registry 现在记录连接器 `last_heartbeat_at`，注册成功会建立初始心跳租约，后续 `record_heartbeat()` 刷新租约；签发 connection token 前会 fail-closed 检查 active 状态与 heartbeat TTL，过期连接器返回 `CONNECTOR_HEARTBEAT_EXPIRED`。Enrollment token 也可绑定连接器 mTLS 证书指纹，签发 token 时若 presented certificate fingerprint 不匹配会返回 `CONNECTOR_MTLS_CERTIFICATE_MISMATCH`；也可绑定 attestation nonce/digest，注册请求缺失或不匹配 attestation 会 fail-closed 拒绝。Connector Registry 已支持 active connector public key rotation，并记录 previous/current fingerprint 与轮换时间；inactive 或 revoked connector 轮换 fail-closed。当前已补持久化 Connector 管理 API：`GET/POST /api/v1/connectors/`、`POST /api/v1/connectors/{connector_id}/heartbeat` 与 `POST /api/v1/connectors/{connector_id}/rotate-key`，按当前租户收敛，响应只暴露运行态、能力声明和绑定布尔值，不返回 enrollment token、attestation digest 或私钥材料。后端包内已提供轻量 `ConnectorSdkClient`，覆盖创建 connector、heartbeat 与 key rotation，并把统一错误响应映射为不泄露 access token 的 SDK 异常。

Phase 4 #t46 已启动会话录制与命令检索后端基础：新增租户隔离的 `SessionRecording` 与 `SessionCommandEvent` 持久化模型，并提供 `POST /api/v1/sessions/{session_id}/recordings`、`POST /api/v1/session-recordings/{recording_id}/commands`、`POST /api/v1/connectors/{connector_id}/session-recordings/{recording_id}/commands`、`GET /api/v1/session-recordings/{recording_id}/commands`、`POST /api/v1/session-recordings/{recording_id}/close` 与 `GET /api/v1/session-recordings/commands?query=...`。命令事件、连接器上报、录制命令时间线和录制关闭按当前租户收敛，inactive 或跨租户 connector fail-closed，跨租户或已关闭录制返回 `SESSION_RECORDING_NOT_FOUND`，命令输出摘要会脱敏 token/password/secret/credential 赋值文本；命令检索在 PostgreSQL 上使用 `to_tsvector` / `plainto_tsquery` 与 GIN 索引，在 SQLite 测试环境保留 `ILIKE` fallback；前端 `/sessions` 已提供按 Recording ID 加载的只读回放命令时间线入口。

Phase 4 #t47 已启动 WebHook / 通知中心后端基础：新增租户隔离的 `WebhookEndpoint` 持久化模型与 `GET/POST /api/v1/webhook-endpoints/`。接口按当前用户租户收敛 endpoint 列表和创建行为，响应只暴露 webhook 名称、URL、事件类型、状态以及 signing secret 是否已配置，不返回 signing secret 明文或摘要。当前已补 `NotificationRule` 持久化模型与 `GET/POST /api/v1/notification-rules/`，规则必须引用当前租户 active WebHook endpoint；并新增 `NotificationDelivery` 队列记录与 `POST /api/v1/notification-rules/{rule_id}/deliveries`、`GET /api/v1/notification-deliveries/`。`NotificationDeliveryWorker` 已提供到期投递、失败重试、最大次数后 dead-letter 的服务契约，并可通过 `HttpWebhookNotificationSender` 向 HTTPS WebHook endpoint 投递已脱敏 payload，非 2xx 或网络错误会 fail-closed 进入重试/死信状态，不向响应或错误泄露 payload、secret 或下游响应体。IM sender 和多级审批仍是后续切片。

Phase 4 #t48 已启动 JIT 策略模板 / 审批策略 DSL 后端基础：现有 `ApprovalPolicyModel` 已通过 `GET/POST /api/v1/workflows/approval-policies` 暴露租户隔离的策略模板管理 API，创建与列表均要求 `workflow:admin` 或 `admin` 权限。接口使用当前用户 `tenant_id` 写入和读取，不接受前端传入 tenant；响应只返回资源 selector、action selector、审批人、MFA、TTL、风险级别与灰度百分比元数据。`PolicyDecisionService` 已可接收 approval policy template，将匹配 selector 且落入 deterministic rollout bucket 的 session 请求 fail-closed 转为 `APPROVAL_REQUIRED` 并返回审批策略 obligations；`POST /api/v1/workflows/approval-policies/simulate` 可在当前租户内复用同一决策引擎做策略模拟，不接受跨租户策略探测。当前已补 approval policy family/version 基础：`POST /api/v1/workflows/approval-policies/{policy_id}/versions` 可在当前租户内创建递增版本并停用旧 active 版本，列表与模拟默认只读取 active/latest 版本；`POST /api/v1/workflows/approval-policies/{policy_id}/rollback` 可把同租户同 family 显式回滚到指定版本；策略支持 `rollout_percentage` 做 0-100 deterministic 灰度命中，并已支持 DSL `context_equals` 精确匹配、`context_in` 枚举匹配、`context_number_gt` / `context_number_gte` / `context_number_lt` / `context_number_lte` 数值阈值匹配、`context_number_between` 数值闭区间匹配、`context_number_not_between` 数值闭区间排除匹配、`context_not_equals` 排除匹配、`context_not_in` 枚举排除匹配、`context_exists` 字段存在性检查、`context_contains` 字符串包含匹配、`context_not_contains` 字符串包含排除匹配、`context_starts_with` 字符串前缀匹配、`context_ends_with` 字符串后缀匹配、`context_matches_regex` 正则完整匹配、`context_ip_in_cidr` 来源 IPv4/IPv6 CIDR 网段匹配、`context_time_between` 请求时间窗口匹配、`context_time_not_between` 请求时间排除窗口匹配以及 `all` / `any` 递归组合表达式，context 不匹配、数值缺失、非法或区间无效、字段缺失/null、字符串字段缺失/非字符串、正则为空/非法、IP/CIDR 缺失或非法、时间值/窗口格式非法、或组合表达式结构非法时 fail-closed 为 `NO_MATCHING_POLICY` 且响应不回显 DSL 条件。更多 DSL 操作符仍是后续切片。

Phase 4 #t49 已启动 SIEM / 告警 / 报表中心基础：`GET /api/v1/audits/reports/summary` 返回当前租户审计事件 total、severity、category、SIEM delivery 状态和高危计数聚合。Phase 5 #t54 已启动合规报表导出基础：`GET /api/v1/audits/reports/compliance` 返回当前租户指定模板的事件 ID 列表、hash chain 起止、报告期间、报表签名、签名算法/key id、正式 JSON 导出格式元数据，以及 WORM 归档元数据 `worm_record_id`、`worm_sequence_number` 和 `worm_content_hash`。接口复用 `audit:read` 权限，按当前用户 `tenant_id` 收敛，不返回 metadata、message、resource_id、session_id 或任何凭据相关明细字段；后端已保留可替换 compliance report signer 边界，默认本地 HMAC signer，也可通过 `COMPLIANCE_REPORT_SIGNER_PROVIDER=external-hmac`、`COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID` 和 `COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET` 切换到外部签章 adapter foundation，缺少外部 key id 或 signing secret 时 fail-closed；WORM 归档默认使用内存 append-only store，也可通过 `COMPLIANCE_REPORT_WORM_ARCHIVE_PROVIDER=external-http`、`COMPLIANCE_REPORT_WORM_ARCHIVE_URL` 和 `COMPLIANCE_REPORT_WORM_ARCHIVE_TOKEN` 接入外部 HTTPS 归档服务，外部请求只发送报表摘要、hash chain、签名元数据和 `worm_content_hash`，不发送原始审计明细或 secret，缺配置、非 HTTPS、非 2xx 或无效响应时 fail-closed；真实云 KMS/证书签章服务接入仍待后续切片。前端 `/audits` 审计页已展示报表总事件、高危事件和 SIEM failed 聚合卡片，并提供 SOC2 合规报表 JSON 下载入口，使用后端返回的安全文件名和 vendor JSON media type，不展示原始审计 metadata。

Phase 4 #t50 已启动 Vault 生产级后端基础：`app.vault.provider` 现在提供 `KmsKeyProvider` 协议与 `EnvelopeEncryptedSecretProvider`，每条 secret 使用随机 32 字节 data key 加密，再通过 KMS provider 包装 data key 保存；解密时必须成功 unwrap data key，KMS 拒绝时 fail-closed。Envelope provider 已支持可替换 `SecretRecordStore`，并新增 `SecretRecordModel` 与 `SqlAlchemySecretRecordStore`，可在数据库中持久化 envelope record，同时不保存凭据明文；`LocalKmsEnvelopeKeyProvider` 已提供本地 AES-GCM wrapped DEK adapter，可用 base64 形式的 32 字节 `VAULT_LOCAL_KMS_MASTER_KEY` 装配；`unwrap_after_approval()` 已提供审批后解包 guard，要求审批当前有效、携带 grant/workflow 标识，并显式绑定目标 secret。当前仍不包含真实云 KMS/HSM/Vault adapter 或 break-glass 流程。

Phase 4 #t51 已启动可观测性基础：后端提供 Prometheus 文本格式 `GET /metrics`，并通过 HTTP middleware 记录请求总数与延迟 histogram，指标标签仅包含 method、路由模板 path 和 status_code，不写入 token、secret、连接串或请求/响应正文。OpenTelemetry 分布式追踪、Loki 日志管道和更完整的部署暴露策略仍是后续切片。

Phase 4 #t52 已启动 Automation Worker 队列、消费循环与调度 API 基础：`AutomationJobQueue` 使用 Redis Streams 风格 `xadd` 写入 JSON-only 后台任务消息，当前白名单任务类型为 `asset.scan`、`credential.rotate` 和 `ansible.playbook`。队列契约拒绝未知任务类型和 password/token/secret/private key 等敏感 payload 字段，不使用 pickle 或任意 Python 对象派发。`AutomationWorker` 可通过 Redis Streams consumer group 读取消息、按 job type 分发到显式 handler，并仅在 handler 成功后 ack。`POST /api/v1/automation/jobs/asset-scans` 可按当前用户租户调度 `asset.scan` 作业，payload 只包含 asset id 与 scan profile；`POST /api/v1/automation/jobs/credential-rotations` 会先按当前用户租户和项目范围确认账号可见，再调度 `credential.rotate` 作业且 payload 不包含 secret；`POST /api/v1/automation/jobs/playbooks` 可调度 `ansible.playbook` 作业，payload 只包含 playbook 名称、目标资产 ID 列表和 check mode，额外字段 fail-closed。当前已补 `AssetScanWorkerHandler`，消费 `asset.scan` 消息时按当前租户确认 active asset，并只把不含 legacy credential 的目标摘要传给显式扫描执行器；并补 `CredentialRotateWorkerHandler`，消费 `credential.rotate` 消息时按当前租户确认 active account，创建轮换记录并调用显式改密执行器，队列 payload 不携带 secret；并补 `AnsiblePlaybookWorkerHandler`，消费 `ansible.playbook` 消息时按当前租户确认 active 目标资产，只把无凭据目标摘要、playbook 名称和 check mode 传给显式 runner 契约；`LocalAnsiblePlaybookRunner` 已提供本地 `ansible-playbook` adapter、playbook root 路径收敛、临时 JSON inventory 渲染、check mode 传递、不继承 secret/token 环境变量的运行目录沙箱基础、执行超时和超时子进程回收，并可通过 `ANSIBLE_PLAYBOOK_ROOT`、`ANSIBLE_RUNTIME_ROOT`、`ANSIBLE_PLAYBOOK_EXECUTABLE`、`ANSIBLE_PLAYBOOK_TIMEOUT_SECONDS`、`ANSIBLE_PLAYBOOK_MEMORY_LIMIT_MB`、`ANSIBLE_PLAYBOOK_CPU_LIMIT_SECONDS` 装配；CPU/内存限制在支持 POSIX `setrlimit` 的本地执行环境中应用于子进程；`AutomationJobRun` 已记录 `ansible.playbook` 执行的 running/completed/failed 状态、message id、请求人、playbook 名称、check mode、目标数量和脱敏错误码，不保存 inventory、stdout、stderr 或 secret payload，并通过 `GET /api/v1/automation/jobs/runs` 按当前租户只读查询执行状态元数据。

## Phase 5 高可用与水平扩展

Phase 5 #t53 已启动无状态 Core 的前置切片：Session connection token store 现在可通过 `SESSION_CONNECTION_TOKEN_STORE=redis` 切换到 Redis-backed 单次消费存储，使用 `REDIS_URL` 与 `SESSION_CONNECTION_TOKEN_REDIS_KEY_PREFIX` 装配。Redis 模式只保存 token digest key 和 JSON 元数据，签发时使用 TTL，消费时通过 Redis `GETDEL` 原子删除，避免多副本下 token 签发和消费必须落在同一后端进程。默认仍为 `memory`，本地开发和单副本部署无需额外 Redis 配置。后端 Redis client 已支持 `REDIS_MODE=single|sentinel|cluster`，可通过 `REDIS_SENTINEL_URLS` / `REDIS_SENTINEL_MASTER_NAME` 或 `REDIS_CLUSTER_URLS` 装配 Sentinel/Cluster。Helm chart 已提供可选 HPA 模板；启用 `autoscaling.enabled=true` 时必须同时设置 `config.sessionConnectionTokenStore=redis`，否则模板渲染 fail-closed。数据库读副本 foundation 已提供可选 `DATABASE_READ_REPLICA_URL`，默认空值时读 session factory 复用写库 engine；资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、认证态用户详情 `/api/v1/auth/me`、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已接入 read session dependency；audit events/summary/compliance GET 已随 Phase 6 #t61 改为数据库读取，但 `AuditService` **自管读写会话**（写走主库、读走只读副本），因此不挂请求级 DB 依赖，在数据库路由清单测试中归类为「自管会话」而非早期的「DB-free 例外」；Helm 通过 Secret 注入该连接串。

## Phase 6 JumpServer 核心功能对标

Phase 6 的任务分解、验收标准与完成度矩阵以 [`docs/architecture/10-master-evaluation-and-roadmap.md`](docs/architecture/10-master-evaluation-and-roadmap.md)（v2.4）为唯一权威来源。以下为已落地切片的入口索引。

### M0 前置阻塞项（已全部关闭）

Phase 6 #t60 已建立 Alembic 迁移基线：`backend/alembic/` 下的基线 revision 覆盖全部 ORM 模型，`scripts/check-migrations.sh` 提供无外部数据库依赖的一致性门禁（临时 aiosqlite 库执行 `upgrade head` → `alembic check` → `downgrade base`），并已接入 CI backend-quality。对真实 PostgreSQL 执行 `alembic check` / autogenerate 会因表达式索引的 REGCONFIG 渲染失败，故一致性门禁只走 sqlite，生产 PostgreSQL 只执行 `upgrade`。

Phase 6 #t61 已把审计事件从进程内列表改为数据库 append-only：`AuditEventModel` 以 `UNIQUE(tenant_id, sequence_number)` 在库层强制有序，per-tenant hash chain 通过 `SELECT … FOR UPDATE` 串行化序号与 previous_event_hash。审计 sink 会被存储无关的 workflow / session service 调用，因此 `AuditService` 自管读写会话，不透传调用方的请求级 db。

Phase 6 #t62 已把会话网关状态落库：`SessionModel` 与 `SqlAlchemySessionStore` 沿用 #t61 的自管会话约定，写流程内的读走主库，仅按 subject 的列表查询走只读副本，与 #t46 `SessionRecording` 经 session_id 逻辑关联。

### M1 资产树与资产授权（#t64 已完成）

Phase 6 #t64 已落地 `NodeModel` 资产树、`AssetPermissionModel` 资产/节点授权和 `Asset.node_id` 挂载映射。节点保存祖先链，资产直接授权或非根节点授权沿树继承；未分组资产不继承节点授权，过期授权不匹配。授权主体支持 `user` / `user_group`，并记录账号、协议、动作、有效期和 `from_ticket` 来源工单。所有授权与资产树读取经 `scoped_select()` 按租户收敛，`PolicyDecisionService.explain_trace` 记录命中授权和继承路径；`/api/v1/assets/` 使用面只返回当前用户有效 connect 资产，admin 不绕过授权。详见 [`docs/site/asset-tree-authorization.md`](docs/site/asset-tree-authorization.md) 和 [`docs/api-contract.md`](docs/api-contract.md) 的 #t64 契约。

### M1 RBAC 角色权限（#t63 已完成）

Phase 6 #t63 已落地 `RoleModel` / `RoleBindingModel` / `UserGroupModel` / `ObjectPermissionModel`，覆盖 system / org 双 scope、内置角色（系统管理员 / 组织管理员 / 审计员 / 普通用户）、用户组与对象级权限。`RbacService.resolve_effective_rbac()` 在登录与 refresh 时将 `permissions`、`menu_permissions`、`group_ids` 写入 JWT，与既有 `require_permission` 和 `scoped_select` 路径兼容；无绑定时 superuser 回退管理员权限集，普通用户回退 MVP 权限集。管理 API 位于 `/api/v1/rbac/*`。详见 [`docs/site/rbac.md`](docs/site/rbac.md)。

### M3 真实连接通道（SSH / K8s 已走通）

Phase 6 #t69 已在 `backend/app/connectors/` 落地连接器侧真实 SSH 通道，基于纯 Python `asyncssh`，不 fork 任何 `ssh` / `sshpass` 子进程：SSH exec 通道、会话编排、命令事件投递 sink、PTY 交互式会话（从键盘输入流重建命令并带读超时）、SFTP 传输与带 sha256 的传输审计事件、主机密钥采集（scan → 审批 → 固定），以及到会话网关的接线。四条安全约束逐条由 `backend/tests/connectors/test_ssh_channel.py` 断言：强制现代算法白名单、私钥仅内存加载、凭据不经命令行且关闭 agent 与默认密钥扫描、主机密钥严格校验且绝不 trust-on-first-use。详见 [`docs/site/connectors-ssh.md`](docs/site/connectors-ssh.md)。

Phase 6 #t72 已落地 K8s exec 通道 `backend/app/connectors/k8s_exec.py`，走 WebSocket `v4.channel.k8s.io` 子协议做 stdin/stdout/stderr/error 多路复用，复用同一条命令事件管线；namespace 作用域在建连前强制、API Server 证书由预置 CA 严格校验且拒绝 trust-on-first-use、bearer token 仅经 `Authorization` 头传递不进 URL 或命令行。端到端测试使用进程内 wss server 与自签证书，无外部集群依赖。详见 [`docs/site/connectors-k8s.md`](docs/site/connectors-k8s.md)。

> 连接器路由默认仍为 `NoopConnectorScheduler`；生产启用需要先落地桥接资产注册表与凭据保险库的真实 `SessionConnectionResolver`。

### M1 ACL 访问控制（#t65 部分完成）

Phase 6 #t65 已落地命令过滤 ACL 与数据脱敏规则，判定统一进 `PolicyDecisionService`，不在路由或连接器旁路。命令过滤按优先级 1-100（小者优先）取首个命中者，语义是**已授权会话之上的 deny-overlay**——无 ACL 命中时默认放行；数据脱敏则是转换而非决策，**累计应用**全部命中规则。两者的规则集通过 `backend/app/policy/repository.py` 的 `AclRepository` 经租户 scope helper `scoped_select` 从数据库加载，并已接入 SSH exec、SSH PTY 和 K8s exec 的执行前守卫及命令事件入库。详见 [`docs/site/acl-command-filter.md`](docs/site/acl-command-filter.md) 与 [`docs/site/acl-data-masking.md`](docs/site/acl-data-masking.md)。

> 当前仍缺登录 ACL、资产登录 ACL 和连接方式 ACL；命令过滤与脱敏的执行前/入库接线已完成，连接器路由默认仍为 `NoopConnectorScheduler`，生产真实会话启用仍需 #t69 的资产注册表与凭据 resolver。
