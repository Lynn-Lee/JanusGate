# JanusGate 操作 Runbook

本页把安装手册与部署基线中的操作步骤整理为可交接的运行清单。真实生产发布前仍以 `deploy/README.md` 的部署与回滚基线为准。

## Operation evidence manifest

可交接的操作证据字段固定在 `docs/site/fixtures/operation-runbook-evidence.json`。该 manifest 把 release、多副本 smoke、rollback 和 secret handling 清单映射到必须记录的命令、结果和密钥边界，随 `scripts/build-docs-site.sh dist/docs-site` 发布到 `fixtures/operation-runbook-evidence.json`，便于审计或外部交付复核。

## License operations evidence manifest

License / Edition 运营证据字段固定在 `docs/site/fixtures/license-operations-evidence.json`，并随 `scripts/build-docs-site.sh dist/docs-site` 发布到 `fixtures/license-operations-evidence.json`。该 manifest 覆盖 external-http 授权服务 SLA、fail-closed drill、timeout budget、目标环境、升级联系人、key custody owner、rotation cadence 和 revocation process。

记录证据时只保存配置状态、负责人和演练结果。不得在 run log、截图、manifest 或 Git 中保存 license key、signing secret、`JANUSGATE_LICENSE_VALIDATION_TOKEN`、私钥、原始授权 payload 或外部授权服务返回的客户数据。

## Release checklist

1. 确认 `dev` 已合并目标变更，且 CI 通过。
2. 确认 `backend/pyproject.toml`、`backend/app/main.py`、`deploy/helm/janusgate/Chart.yaml` 和 `deploy/helm/janusgate/values.yaml` 使用一致的 `MAJOR.MINOR.PATCH` 版本。
3. 运行发布门禁：

   ```bash
   git diff --check
   scripts/phase5-release-readiness-smoke.sh
   scripts/build-docs-site.sh dist/docs-site
   scripts/phase5-docs-browser-screenshots-smoke.sh
   ```

4. 如包含数据库迁移，先备份 PostgreSQL，并记录备份对象、时间点、目标版本和操作者。
5. 打 `v*` tag 触发 release pipeline，等待 GHCR image、SBOM 和 Cosign signing 结果。
6. 使用 release image tag 执行 Helm upgrade，并记录 Helm revision。
7. 执行 `/health`、登录、JIT grant、connection token、会话创建和审计写入 smoke。

## Docs screenshot checklist

管理员文档截图更新必须保留固定 evidence id 和脱敏边界。

1. 先确认前端回归测试仍覆盖 Settings License / Edition、Audits SOC2 export 和 Sessions recording timeline。
2. 默认 CI 只执行截图证据 wiring smoke：

   ```bash
   scripts/phase5-docs-browser-screenshots-smoke.sh
   ```

3. 需要重新捕获真实浏览器截图时，在可访问前端 dev/test 控制台和 Playwright 环境中执行；脚本会注入 `docs/site/fixtures/admin-screenshot-data.json`，按 `capture_actions` 完成页面操作，并在截图前校验 `must_show` / `must_not_show`：

   ```bash
   JANUSGATE_CAPTURE_DOC_SCREENSHOTS=1 \
   JANUSGATE_FRONTEND_BASE_URL=http://127.0.0.1:5173 \
   scripts/phase5-docs-browser-screenshots-smoke.sh
   ```

4. 捕获后复核 `docs/site/assets/screenshots/live-screenshots/*.png`，不得包含 bearer token、license key、signing secret、连接串、私钥或真实客户数据；根目录 SVG 资产保留为静态发布包脱敏基线证据。
5. 重新运行 `scripts/build-docs-site.sh dist/docs-site`，确认 manifest 仍包含截图资产和 `screenshotCapture`。

## Multi-replica smoke checklist

多副本部署必须先满足共享 token store 和数据库读副本条件。

1. 设置 `SESSION_CONNECTION_TOKEN_STORE=redis`，并确认 Redis 可用。
2. 如启用 `DATABASE_READ_REPLICA_URL`，确认读副本延迟不会影响写后读路径。
3. 执行配置级 smoke：

   ```bash
   scripts/phase5-ha-config-smoke.sh
   ```

4. 在目标 Kubernetes context 中提供真实 PostgreSQL writer/read replica、Redis 和 `SECRET_KEY` 后执行：

   ```bash
   scripts/phase5-k8s-multi-replica-smoke.sh
   ```

5. 验证至少 2 个 ready Pod，并通过 Service port-forward 请求 `/health`。
6. 再跑登录到 connection token 消费的主链路 smoke，确认 token 签发和消费不会落回单进程假设。

## Rollback checklist

应用回滚：

```bash
helm history janusgate -n janusgate
helm rollback janusgate <REVISION> -n janusgate
kubectl rollout status deployment/janusgate -n janusgate
curl -fsS http://localhost:8000/health
```

数据边界：

1. 若本次发布包含数据库迁移，先判断 schema 是否向后兼容。
2. 如果新旧应用不能共用 schema，按备份恢复或显式 down migration runbook 执行。
3. 不把 `helm rollback` 误认为数据回滚。
4. 回滚后重复 `/health`、登录、JIT grant、connection token 和审计写入 smoke。

## Secret handling

- Do not print `SECRET_KEY`、`DATABASE_URL`、`DATABASE_READ_REPLICA_URL`、Redis 密码、license key 或 signing secret。
- 不把密钥写入 Git、ConfigMap、普通 values 文件或 run log。
- Helm 生产部署优先使用 `secret.existingSecret`。
- smoke 临时 values 文件必须限制为 `0600` 权限，并在脚本退出时清理。
