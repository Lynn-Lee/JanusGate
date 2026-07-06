# JanusGate 安装手册

本手册覆盖最小可重复部署路径。生产环境不得把真实密钥、数据库连接串或 license signing secret 写入 Git、ConfigMap 或普通 values 文件。

## 本地 Docker Compose

1. 生成 `.env`：

   ```bash
   cp .env.example .env
   python -c 'import secrets; print(secrets.token_hex(32))'
   ```

2. 将生成值写入 `.env` 的 `SECRET_KEY`，并设置 `POSTGRES_PASSWORD`。

3. 启动：

   ```bash
   docker compose up --build -d
   curl -fsS http://localhost:8000/health
   ```

4. 停止：

   ```bash
   docker compose down
   ```

## Helm 安装

先创建 Secret：

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

## 多副本前置条件

- `SESSION_CONNECTION_TOKEN_STORE=redis`。
- Redis 连接可用，并已通过 connection token 主链路 smoke。
- 如启用 `DATABASE_READ_REPLICA_URL`，确认读副本延迟不会影响需要强一致的写后读路径。
- Helm HPA 在 memory token store 下会 fail-closed，必须先切到 Redis-backed token store。

## 回滚入口

```bash
helm history janusgate -n janusgate
helm rollback janusgate <REVISION> -n janusgate
kubectl rollout status deployment/janusgate -n janusgate
curl -fsS http://localhost:8000/health
```

涉及数据库迁移时，先按 `deploy/README.md` 的发布与回滚 runbook 判断 schema 是否向后兼容。
