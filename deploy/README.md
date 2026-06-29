# JanusGate 部署与交付基线

本目录归 DevOps owner 维护，覆盖 CI/CD、Docker Compose、Helm 以及发布/回滚操作。共享协作规则仍以 `origin/dev` 为准：开工前先 `git pull --ff-only origin dev`，跨 owner 目录修改前先在频道确认。

## 1. CI/CD 门禁

GitHub Actions 工作流位于 `.github/workflows/ci.yml`，当前包含：

- `ruff check .`：Python lint / import ordering
- `mypy app`：后端类型检查
- `pytest`：后端单元测试与 smoke 测试
- `bandit -q -r app`：应用代码安全扫描
- `pip-audit --skip-editable`：依赖漏洞扫描
- Docker Buildx：构建 backend 镜像；仅 `v*` tag 会推送到 GHCR
- `helm lint deploy/helm/janusgate`：Helm chart 基础校验

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

停止并保留数据：

```bash
docker compose down
```

清理本地数据卷：

```bash
docker compose down -v
```

## 3. Helm 部署

生产或测试集群不要把真实密钥写入 Git。推荐先创建 Kubernetes Secret：

```bash
kubectl create namespace janusgate
kubectl create secret generic janusgate-backend-secret \
  -n janusgate \
  --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
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
  --set config.databaseUrl='postgresql+asyncpg://janusgate:REDACTED@postgresql:5432/janusgate' \
  --set config.redisUrl='redis://redis-master:6379/0'
```

验证：

```bash
kubectl rollout status deployment/janusgate -n janusgate
kubectl port-forward svc/janusgate 8000:8000 -n janusgate
curl -fsS http://localhost:8000/health
```

## 4. 发布与回滚

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

## 5. 密钥与环境变量边界

- `.env`、真实 Helm values、Kubernetes Secret 明文不得提交仓库。
- 本地开发通过 `.env` 注入；Compose 使用 `${VAR:?message}` 对关键变量 fail-closed。
- Kubernetes 通过 Secret/ConfigMap 注入；共享环境优先使用 `secret.existingSecret`。
- CI 不依赖生产密钥；依赖扫描和镜像构建不得输出 token、数据库密码或 JWT secret。
- backend 镜像以非 root 用户运行，并启用 no-new-privileges/read-only root filesystem（Compose/Helm 环境）。
