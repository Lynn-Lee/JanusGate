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
- Docker Buildx：构建 backend 镜像；仅 `v*` tag 会推送到 GHCR
- `helm lint deploy/helm/janusgate`：Helm chart 基础校验
- `docker compose config`：Compose 配置渲染 smoke，验证关键环境变量 fail-closed 约束可被 CI 测试值满足
- `scripts/phase3-compose-health-smoke.sh`：启动 Compose backend 及依赖并请求 `/health`，退出时清理本轮容器和卷
- `helm template janusgate deploy/helm/janusgate`：Helm chart 渲染 smoke，防止只通过 lint 但模板输出损坏

安全边界：工作流只使用 `GITHUB_TOKEN` 在 release tag 场景推送 GHCR；不会读取或打印业务密钥。CI 中的 `SECRET_KEY` 是一次性测试值，不用于部署。

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

数据库默认仍使用 `DATABASE_URL` 单写库连接。Phase 5 #t53 起可通过 `DATABASE_READ_REPLICA_URL` 配置可选只读副本；未设置时只读 session factory 复用写库 engine，保持本地开发、Compose 和单副本部署行为不变。资产列表、资产详情、平台列表、账号列表、账号轮换列表、会话录制命令时间线、命令检索、Tenancy Organization/Team/Project 列表、WebHook endpoint 列表、通知规则列表、通知投递列表、Connector 列表、SSH CA 列表、SSH CA trust bundle、SSH certificate 列表、Automation job run 列表、approval policy 列表、Workflow request 列表/详情以及 active JIT grant 列表 GET 路由已接入 read session dependency，写入、删除、连接测试、轮换调度、命令事件上报、录制关闭、WebHook endpoint 创建、通知规则创建、通知投递入队、Connector 创建/心跳/key rotation、SSH CA 创建/禁用、SSH certificate 签发/撤销、Automation job 调度、approval policy 创建/版本/回滚/模拟以及 Workflow request 创建/提交/审批/拒绝/撤销等可能改变状态或要求强一致的路径仍走 writer session。该值属于数据库连接串，Helm 中通过 Secret 注入，不写入 ConfigMap 或普通 values 明文；启用前应确认 PostgreSQL 主从复制延迟不会影响需要强一致的写后读路径。

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

## 5. 发布与回滚

发布最小步骤：

1. 合并已 review 的 PR 到 `dev`。
2. 打 `v*` tag 触发 GHCR 镜像推送。
3. 使用该镜像 tag 执行 `helm upgrade --install`。
4. 执行 `/health` 与核心 API smoke 测试。

回滚：

```bash
helm history janusgate -n janusgate
helm rollback janusgate <REVISION> -n janusgate
kubectl rollout status deployment/janusgate -n janusgate
curl -fsS http://localhost:8000/health
```

如回滚仍失败，优先缩容入口流量或恢复上一版镜像 tag；不要在事故中临时把密钥写入 values 或日志。

## 6. 密钥与环境变量边界

- `.env`、真实 Helm values、Kubernetes Secret 明文不得提交仓库。
- 本地开发通过 `.env` 注入；Compose 使用 `${VAR:?message}` 对关键变量 fail-closed。
- Kubernetes 通过 Secret/ConfigMap 注入；`DATABASE_URL`、`SECRET_KEY`、JWT 配置必须来自 Secret，不能放入 ConfigMap 或普通 values 文件；共享环境优先使用 `secret.existingSecret`。
- CI 不依赖生产密钥；依赖扫描和镜像构建不得输出 token、数据库密码或 JWT secret。
- backend 镜像以非 root 用户运行，并启用 no-new-privileges/read-only root filesystem（Compose/Helm 环境）。
