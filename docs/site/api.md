# JanusGate API 文档

API 文档由稳定契约和自动导出的 OpenAPI 共同组成：

- 稳定契约：`docs/api-contract.md`
- OpenAPI 生成：`scripts/export-openapi-json.sh`
- 默认输出：`docs/site/openapi.json`
- 静态发布包：`scripts/build-docs-site.sh dist/docs-site`

## 生成 OpenAPI

```bash
scripts/export-openapi-json.sh docs/site/openapi.json
```

脚本会从 FastAPI `app.main` 导出 schema，并设置一次性测试 `SECRET_KEY`，避免开发机必须预先写入真实密钥。

## 生成静态发布包

```bash
scripts/build-docs-site.sh dist/docs-site
```

该 smoke 会重新导出 OpenAPI，把 `docs/site/*.md` 复制到发布目录，并写入 `manifest.json`，供后续静态站点生成器或 artifact 发布流程消费。

## 主要 API 分组

- Auth：`/api/v1/auth/login`、refresh、MFA、当前用户和 API key。
- Assets：`/api/v1/assets/`、平台列表和受控连接测试。
- Sessions：`/api/v1/sessions/`、connection token、会话创建与关闭。
- Workflow/JIT：`/api/v1/workflows/requests`、审批、拒绝、撤销和 active grants。
- Accounts：`/api/v1/accounts/` 和账号轮换。
- Tenancy：`/api/v1/tenancy/organizations`、teams、projects。
- SSH CA：`/api/v1/ssh-certificate-authorities/`、trust bundle 和 `/api/v1/ssh-certificates/`。
- Audit：`/api/v1/audits/events`、`/api/v1/audits/reports/summary`、`/api/v1/audits/reports/compliance`。
- Automation：`/api/v1/automation/jobs/asset-scans`、credential rotations、playbooks 和 job runs。
- Admin：`/api/v1/admin/license-summary` 与 admin-only `POST /api/v1/admin/license-config`。
- ACL：`/api/v1/command-filter-acls/` 与 `/api/v1/data-masking-rules/`（租户隔离 CRUD，仅这两类；SSH/K8s/PTY 执行前与命令事件入库均走 PolicyDecisionService）。
- Asset tree / AssetPermission：`/api/v1/asset-nodes/`、`/api/v1/asset-nodes/{node_id}/assets`、`/api/v1/asset-nodes/{node_id}/permissions`、`/api/v1/asset-permissions/by-asset/{asset_id}` 和 `/api/v1/asset-permissions/{permission_id}`；详见[资产树与 AssetPermission](asset-tree-authorization.md)。

## 错误响应

常见错误统一返回 `ErrorResponse`：

```json
{
  "code": "UNAUTHORIZED",
  "message": "用户名或密码错误",
  "detail": "用户名或密码错误",
  "request_id": ""
}
```

前端和外部集成应优先读取 `code` 做稳定分支，不要解析本地化 `message`。
