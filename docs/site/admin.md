# JanusGate 管理员手册

本手册记录当前管理后台和运维侧可依赖的能力边界。更细的 API 字段以 `docs/api-contract.md` 和 OpenAPI 为准。

## 登录与权限

- 所有受保护 API 默认使用 Bearer access token。
- 管理接口要求当前用户具备对应权限或 `admin`。
- 普通用户不能审批自己的 JIT 申请，也不能读取他人的申请、grant、会话或审计详情。

## 资产、账号与会话

- 资产页面用于查看平台、资产和 JIT 申请入口。
- 账号托管页面只展示 Vault `secret_id` 引用，不展示凭据明文。
- 会话页面展示当前用户可见会话，并支持读取指定录制 ID 的命令时间线。
- 会话录制命令摘要会脱敏 token、password、secret、credential 等赋值文本。

## License / Edition

`GET /api/v1/admin/license-summary` 只允许 `admin` 读取当前 configured/effective edition、license status、启用能力和禁用能力。

安全边界：

- 响应不返回 license key。
- 响应不返回 signing secret。
- 响应不返回原始 license payload。
- `JANUSGATE_EDITION=enterprise` 但 license 缺失、过期或签名无效时，effective edition fail-closed 回退到 `community`。

## 审计与报表

- 审计日志页展示当前租户事件列表和报表摘要。
- SOC2 合规报表导出只返回事件 ID、hash chain 边界、签名和 WORM 归档元数据，不返回原始 metadata、message、resource_id 或 session_id 明细。

## 运维门禁

- 本地快速检查优先运行 `git diff --check`。
- 后端改动运行 `ruff check .`、`mypy app` 和 `pytest -q`。
- 前端改动运行 `npm run lint`、`npm run typecheck`、`npm test -- --run` 和 `npm run build`。
- 部署/Helm 改动运行 `docker compose config`、`helm lint deploy/helm/janusgate` 和相关 Phase 5 smoke 脚本。
