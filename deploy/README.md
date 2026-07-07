# JanusGate 部署与交付基线

本目录归 DevOps owner 维护，覆盖 CI/CD、Docker Compose、Helm 以及发布/回滚操作。共享协作规则仍以 `origin/dev` 为准：开工前先 `git pull --ff-only origin dev`，跨 owner 目录修改前先在频道确认。

## 1. CI/CD 门禁

GitHub Actions 工作流位于 `.github/workflows/ci.yml`，当前包含：

- `ruff check .`：Python lint / import ordering
- `mypy app`：后端类型检查
- `pytest`：后端单元测试与 smoke 测试
- `bandit -q -r app`：应用代码安全扫描
- `pip-audit --skip-editable`：依赖漏洞扫描
- `npm ci`：前端依赖按 lockfile 可复现安装
- `npm run lint`：前端 ESLint 检查
- `npm run typecheck`：前端 TypeScript 类型检查
- `npm test -- --run`：前端 Vitest 组件/API smoke 测试
- `npm run build`：前端生产构建检查
- `scripts/phase5-supply-chain-security-smoke.sh`：校验 Phase 5 SBOM、镜像签名和漏洞扫描 CI wiring
- `scripts/phase5-runtime-monitoring-smoke.sh`：校验 Phase 5 运行时加固配置和 `/metrics` 回归契约
- `deploy/monitoring/phase5-runtime-alerts.yaml`：Prometheus 运行时异常告警规则基线，覆盖高 5xx、p95 延迟和 metrics 缺失
- Trivy high/critical vulnerability gate：对仓库文件系统执行高危/严重漏洞扫描
- Docker Buildx：构建 backend 镜像；仅 `v*` tag 会推送到 GHCR
- release tag SBOM：对已推送的 backend 镜像 digest 生成 SPDX JSON SBOM artifact
- release tag Cosign signing：通过 GitHub OIDC 对 backend 镜像 digest 执行 keyless signing
- `helm lint deploy/helm/janusgate`：Helm chart 基础校验
- `docker compose config`：Compose 配置渲染 smoke，验证关键环境变量 fail-closed 约束可被 CI 测试值满足
- `scripts/phase3-compose-health-smoke.sh`：启动 Compose backend 及依赖并请求 `/health`，退出时清理本轮容器和卷
- `helm template janusgate deploy/helm/janusgate` + CI 一次性测试 `secret.secretKey` / `secret.databaseUrl`：Helm chart 渲染 smoke，防止只通过 lint 但模板输出损坏，同时保留默认缺失 secret 时 fail-closed

安全边界：工作流只使用 `GITHUB_TOKEN` 在 release tag 场景推送 GHCR，并通过 GitHub OIDC 给 Cosign keyless signing 换取短期签名身份；不会读取或打印业务密钥。CI 中的 `SECRET_KEY` 是一次性测试值，不用于部署。

## 2. 本地 Docker Compose

首次启动：

```bash
cp .env.example .env
python -c 'import secrets; print(secrets.token_hex(32))'
# 将生成值写入 .env 的 SECRET_KEY，并替换 POSTGRES_PASSWORD
docker compose up --build -d
curl -fsS http://localhost:8000/health
```

Phase 3 部署 smoke 可直接运行：

```bash
scripts/phase3-compose-health-smoke.sh
```

脚本默认使用 `COMPOSE_PROJECT_NAME=janusgate-phase3-smoke`，适合 CI 或一次性本地验证；如未提供 `.env`，脚本会创建一次性测试 env 文件并在退出时删除。Compose 默认只发布 backend `8000`，PostgreSQL/Redis 仅在 Compose 网络内暴露，避免与本机数据库或缓存端口冲突。

停止并保留数据：

```bash
docker compose down
```

清理本地数据卷：

```bash
docker compose down -v
```

## 3. Helm 部署

生产或测试集群不要把真实密钥或含用户名/密码的数据库 DSN 写入 Git、ConfigMap 或普通 values 文件。推荐先创建 Kubernetes Secret：

```bash
kubectl create namespace janusgate
kubectl create secret generic janusgate-backend-secret \
  -n janusgate \
  --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://janusgate:${JANUSGATE_DB_PASSWORD}@postgresql:5432/janusgate" \
  --from-literal=JWT_ALGORITHM="HS256" \
  --from-literal=ACCESS_TOKEN_EXPIRE_MINUTES="30" \
  --from-literal=REFRESH_TOKEN_EXPIRE_DAYS="7"
```

安装或升级：

```bash
helm upgrade --install janusgate deploy/helm/janusgate \
  -n janusgate \
  --set image.repository=ghcr.io/lynn-lee/janusgate-backend \
  --set image.tag=0.1.0 \
  --set secret.existingSecret=janusgate-backend-secret \
  --set config.redisUrl='redis://redis-master:6379/0'
```

验证：

```bash
kubectl rollout status deployment/janusgate -n janusgate
kubectl port-forward svc/janusgate 8000:8000 -n janusgate
curl -fsS http://localhost:8000/health
```

## 4. Connection token store 与多副本部署

默认 session connection token store 仍为后端进程内短期存储，适合本地开发和单副本部署。为避免 token 签发和消费落到不同 Pod，默认 Helm `replicaCount` 保持为 `1`。

Phase 5 #t53 起可通过共享 Redis token store 支撑无状态 Core 前置验证：

```bash
SESSION_CONNECTION_TOKEN_STORE=redis
REDIS_URL=redis://redis-master:6379/0
REDIS_MODE=single
SESSION_CONNECTION_TOKEN_REDIS_KEY_PREFIX=janusgate:session:connection-token:
```

Redis 模式只保存 token digest key 和 JSON 元数据，签发时使用 TTL，消费时通过 Redis `GETDEL` 原子删除，原始 connection token 不写入 Redis。启用多副本前必须确认 Redis 可用性、持久连接配置和 `/api/v1/sessions/connection-token` → `/api/v1/sessions/` 主链路 smoke。

如果必须在 memory store 下临时多副本部署，只允许作为过渡方案并满足以下条件：

1. 入口负载均衡启用粘性会话，确保同一 connection token 生命周期内的签发、消费、撤销请求固定到同一后端实例。
2. 发布前执行 connection token smoke：申请 JIT grant → 签发 connection token → 创建会话 → revoke → 查询审计事件。
3. 回滚时优先回滚到上一版单副本或同样具备粘性策略的版本，避免 token 消费路径跨实例失败。

生产推荐方案是启用 Redis-backed shared token store，要求 token 短 TTL、一次性消费、撤销状态和审计事件在副本间一致；完成 Redis 模式主链路验证前，不把无粘性多副本视为可放行生产形态。

数据库默认仍使用 `DATABASE_URL` 单写库连接。Phase 5 #t53 起可通过 `DATABASE_READ_REPLICA_URL` 配置可选只读副本；未设置时只读 session factory 复用写库 engine，保持本地开发、Compose 和单副本部署行为不变。资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、认证态用户详情 `/api/v1/auth/me`、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已接入 read session dependency；audit events/summary GET 当前仍读取进程内 audit service，不使用 SQLAlchemy session，数据库路由回归测试已将其登记为显式 DB-free 例外。写入、删除、连接测试、登录、2FA、refresh token、MFA/密码/API key 变更、connection token 签发、会话创建/关闭、轮换调度、命令事件上报、录制关闭、WebHook endpoint 创建、通知规则创建、通知投递入队、Connector 创建/心跳/key rotation、SSH CA 创建/禁用、SSH certificate 签发/撤销、Automation job 调度、approval policy 创建/版本/回滚/模拟以及 Workflow request 创建/提交/审批/拒绝/撤销等可能改变状态或要求强一致的路径仍走 writer session。该值属于数据库连接串，Helm 中通过 Secret 注入，不写入 ConfigMap 或普通 values 明文；启用前应确认 PostgreSQL 主从复制延迟不会影响需要强一致的写后读路径。

合规报表默认使用本地 HMAC signer，签名 secret 复用 `SECRET_KEY`。Phase 5 #t54 起可通过配置驱动 external HMAC signer adapter foundation：

```bash
COMPLIANCE_REPORT_SIGNER_PROVIDER=external-hmac
COMPLIANCE_REPORT_EXTERNAL_SIGNING_KEY_ID=kms-key-prod-1
COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET=<external-signing-secret>
```

`COMPLIANCE_REPORT_EXTERNAL_HMAC_SECRET` 属于 signing secret，必须通过 Secret 注入，不写入 ConfigMap、values 明文或日志。启用 external HMAC provider 时缺少 key id 或 signing secret 会 fail-closed；真实云 KMS/证书签章服务接入仍需后续 adapter。

合规报表 WORM 归档默认使用进程内 append-only store，适合本地开发和 API 契约 smoke。需要接入外部 WORM 归档服务时可启用 `external-http` adapter foundation：

```bash
COMPLIANCE_REPORT_WORM_ARCHIVE_PROVIDER=external-http
COMPLIANCE_REPORT_WORM_ARCHIVE_URL=https://worm-archive.example.com/v1/compliance-reports
COMPLIANCE_REPORT_WORM_ARCHIVE_TOKEN=<worm-archive-bearer-token>
COMPLIANCE_REPORT_WORM_ARCHIVE_TIMEOUT_SECONDS=5
```

`COMPLIANCE_REPORT_WORM_ARCHIVE_TOKEN` 必须通过 Secret 注入，不写入 ConfigMap、values 明文或日志。外部 adapter 只发送合规报表摘要、hash chain、签名元数据和 `worm_content_hash`，不发送原始 audit metadata、message、resource_id、session_id、token、secret 或连接串；URL 非 HTTPS、缺 token、外部服务非 2xx 或响应缺少 `record_id` / `sequence_number` 时会 fail-closed。

License / Edition 默认使用 community。Phase 5 #t58 起，enterprise license 支持三种验签模式：

```bash
# HMAC foundation
JANUSGATE_EDITION=enterprise
JANUSGATE_LICENSE_VERIFIER=hmac
JANUSGATE_LICENSE_KEY=<license-key>
JANUSGATE_LICENSE_SIGNING_SECRET=<license-signing-secret>

# Offline public-key foundation
JANUSGATE_EDITION=enterprise
JANUSGATE_LICENSE_VERIFIER=ed25519
JANUSGATE_LICENSE_KEY=<license-key>
JANUSGATE_LICENSE_PUBLIC_KEY=<base64-raw-ed25519-public-key>

# External commercial license service foundation
JANUSGATE_EDITION=enterprise
JANUSGATE_LICENSE_VERIFIER=external-http
JANUSGATE_LICENSE_KEY=<opaque-license-key>
JANUSGATE_LICENSE_VALIDATION_URL=https://license.example.com/v1/validate
JANUSGATE_LICENSE_VALIDATION_TOKEN=<service-bearer-token>
JANUSGATE_LICENSE_VALIDATION_TIMEOUT_SECONDS=5
```

`JANUSGATE_LICENSE_KEY`、`JANUSGATE_LICENSE_SIGNING_SECRET`、`JANUSGATE_LICENSE_VALIDATION_TOKEN` 和任何签名私钥都不得写入 Git、ConfigMap、values 明文或日志。Ed25519 模式只需要在部署环境注入公钥；签名私钥必须保留在外部授权系统。`external-http` 模式只向 HTTPS validation endpoint 发送 opaque license key，缺少 URL、服务不可用、非 200 或无效 payload 都 fail-closed 回退 community。

管理员也可以通过 `POST /api/v1/admin/license-config` 写入当前激活 license 配置；后续 `GET /api/v1/admin/license-summary` 会优先读取 DB 中的持久化配置，无记录时回退上述环境变量。该接口只返回脱敏摘要，不回显 license key、signing secret、公钥或原始 payload。生产环境仍应把数据库备份、访问控制和审计日志纳入 license 配置保护范围。

后端 Redis client 支持三种部署形态：

```bash
# 单节点，默认
REDIS_MODE=single
REDIS_URL=redis://redis-master:6379/0

# Sentinel
REDIS_MODE=sentinel
REDIS_SENTINEL_URLS=redis://redis-sentinel-0:26379/0,redis://redis-sentinel-1:26379/0
REDIS_SENTINEL_MASTER_NAME=mymaster

# Cluster
REDIS_MODE=cluster
REDIS_CLUSTER_URLS=redis://redis-cluster-0:6379/0,redis://redis-cluster-1:6379/0
```

`REDIS_MODE=sentinel` 时必须提供 `REDIS_SENTINEL_URLS` 和 master name；`REDIS_MODE=cluster` 时必须提供 `REDIS_CLUSTER_URLS`。Helm 可通过 `config.redisMode`、`config.redisSentinelUrls`、`config.redisSentinelMasterName`、`config.redisClusterUrls` 和 `config.redisSocketTimeoutSeconds` 注入这些配置。

Helm chart 支持可选 HPA，但默认关闭。启用前必须显式切换到 Redis-backed connection token store，避免多副本下签发与消费落到不同 Pod：

```bash
helm upgrade --install janusgate deploy/helm/janusgate \
  -n janusgate \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=2 \
  --set autoscaling.maxReplicas=5 \
  --set config.sessionConnectionTokenStore=redis \
  --set config.redisMode=sentinel \
  --set config.redisSentinelUrls='redis://redis-sentinel-0:26379/0,redis://redis-sentinel-1:26379/0' \
  --set config.redisSentinelMasterName=mymaster \
  --set config.redisUrl='redis://redis-master:6379/0'
```

如果 `autoscaling.enabled=true` 但 `config.sessionConnectionTokenStore` 仍为 `memory`，Helm 模板会拒绝渲染。

Phase 5 高可用配置 smoke 可在本地或 CI 运行：

```bash
scripts/phase5-ha-config-smoke.sh
```

该脚本执行 `docker compose config`，确认 Compose read-replica 环境变量可渲染；随后验证 Helm 在 `autoscaling.enabled=true` 且 `config.sessionConnectionTokenStore=memory` 时 fail-closed；最后渲染 Redis-backed connection token store、Sentinel Redis 配置、HPA 和 `DATABASE_READ_REPLICA_URL` Secret 注入的多副本配置。它是配置级 smoke，不会启动真实多副本集群；发布前仍需在目标 Kubernetes 环境执行 connection token 主链路和读副本延迟验收。

真实 Kubernetes 多副本 smoke 使用当前 kube context 部署 Helm release，等待 Deployment rollout 和至少 2 个 ready Pod，并通过 Service port-forward 请求 `/health`：

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export DATABASE_URL='postgresql+asyncpg://janusgate:***@postgres-writer:5432/janusgate'
export DATABASE_READ_REPLICA_URL='postgresql+asyncpg://janusgate_reader:***@postgres-read:5432/janusgate'
export REDIS_URL='redis://redis-master:6379/0'
scripts/phase5-k8s-multi-replica-smoke.sh
```

脚本要求 `helm`、`kubectl` 和 `curl` 可用，当前 Kubernetes 身份可创建 namespace，并且外部 PostgreSQL writer/read replica 与 Redis 已可被集群内 Pod 访问。它会把敏感值写入本地权限为 `0600` 的临时 Helm values 文件，不通过命令行 `--set` 打印 secret；退出时会卸载 smoke release，并在本轮创建 namespace 时删除 namespace。GitHub Actions 中该 smoke 默认不运行；只有显式设置 repository variable `JANUSGATE_RUN_K8S_SMOKE=1` 且提供 `JANUSGATE_SMOKE_SECRET_KEY`、`JANUSGATE_SMOKE_DATABASE_URL`、`JANUSGATE_SMOKE_DATABASE_READ_REPLICA_URL` 和 `JANUSGATE_SMOKE_REDIS_URL` secrets 时才会执行。

## 5. 版本发布与回滚 runbook

发布前检查：

1. 确认 `backend/pyproject.toml`、`backend/app/main.py`、`deploy/helm/janusgate/Chart.yaml` 和 `deploy/helm/janusgate/values.yaml` 的版本号一致，且使用 `MAJOR.MINOR.PATCH` 语义版本。
2. 运行 `scripts/phase5-release-readiness-smoke.sh`，确认 release tag、镜像发布、迁移和回滚文档门禁仍可被 CI 检查。
3. 迁移前备份 PostgreSQL，并记录备份对象、时间点、目标版本和操作者；涉及破坏性 schema 变更时，先在 staging 以生产级数据快照演练恢复。
4. 数据迁移必须通过 Alembic 路径执行；发布前至少运行 `alembic current` 和待发布迁移的 dry-run/离线 SQL 复核，不允许在事故中手写生产 DDL。

发布最小步骤：

1. 合并已 review 的 PR 到 `dev`。
2. 打 `v*` tag 触发 GHCR 镜像推送。
3. 使用该镜像 tag 执行 `helm upgrade --install`。
4. 执行 `/health` 与核心 API smoke 测试。
5. 记录发布版本、镜像 digest、Helm revision、迁移版本和 smoke 结果。

回滚：

```bash
helm history janusgate -n janusgate
helm rollback janusgate <REVISION> -n janusgate
kubectl rollout status deployment/janusgate -n janusgate
curl -fsS http://localhost:8000/health
```

数据回滚边界：

- 只要本次发布包含数据库迁移，先评估 schema 是否向后兼容；若新旧应用不能共用 schema，必须按备份恢复或显式 down migration runbook 执行，不把 `helm rollback` 误认为数据回滚。
- 回滚后重复执行 `/health`、登录、JIT grant、connection token 和审计写入 smoke，确认旧版本应用仍能读写当前 schema。
- 如回滚仍失败，优先缩容入口流量或恢复上一版镜像 tag；不要在事故中临时把密钥写入 values 或日志。

## 6. 密钥与环境变量边界

- `.env`、真实 Helm values、Kubernetes Secret 明文不得提交仓库。
- 本地开发通过 `.env` 注入；Compose 使用 `${VAR:?message}` 对关键变量 fail-closed。
- Kubernetes 通过 Secret/ConfigMap 注入；`DATABASE_URL`、`SECRET_KEY`、JWT 配置必须来自 Secret，不能放入 ConfigMap 或普通 values 文件；共享环境优先使用 `secret.existingSecret`。
- CI 不依赖生产密钥；依赖扫描和镜像构建不得输出 token、数据库密码或 JWT secret。
- backend 镜像以非 root 用户运行，并启用 no-new-privileges/read-only root filesystem（Compose/Helm 环境）。

## 7. 运行时监控与告警

Phase 5 #t56 提供 Prometheus 告警规则基线：`deploy/monitoring/phase5-runtime-alerts.yaml`。当前规则只依赖 JanusGate 后端 `/metrics` 已暴露的 HTTP request counter 和 latency histogram，避免读取请求体、响应体、token、secret 或连接串。

最小告警覆盖：

1. `JanusGateRuntimeHigh5xxRate`：5 分钟窗口内 5xx 比例高于 2%，持续 10 分钟。
2. `JanusGateRuntimeHighP95Latency`：5 分钟窗口 p95 延迟高于 1.5 秒，持续 10 分钟。
3. `JanusGateRuntimeMetricsEndpointMissing`：Prometheus 未收到 JanusGate HTTP 指标，持续 5 分钟。

部署到真实监控平台前，需要把该规则文件接入目标 Prometheus/Alertmanager 管理方式，并在目标环境记录一次 `promtool check rules` 或等价校验结果。仓库内 smoke 只校验规则文件与现有 `/metrics` 合约的 wiring，不假设本地存在 Prometheus。

Phase 5 #t56 runtime alert evidence manifest 位于 `docs/site/fixtures/runtime-alert-evidence.json`，用于真实 Alertmanager route drill 时固定证据字段：目标环境、规则校验命令、receiver owner、路由策略、测试告警时间、通知送达结果、ack owner 和升级联系人。记录时只保存配置状态和演练结果，不保存 Prometheus 凭据、receiver token、Alertmanager webhook payload、通知平台 secret 或客户指标明细。该 manifest 会随 `scripts/build-docs-site.sh dist/docs-site` 发布，并由 `scripts/phase5-runtime-monitoring-smoke.sh` 检查 schema 和敏感字段边界。
