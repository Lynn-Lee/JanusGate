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

Phase 4 #t45 已启动连接器/边缘网关生产级信任链：Connector Registry 现在记录连接器 `last_heartbeat_at`，注册成功会建立初始心跳租约，后续 `record_heartbeat()` 刷新租约；签发 connection token 前会 fail-closed 检查 active 状态与 heartbeat TTL，过期连接器返回 `CONNECTOR_HEARTBEAT_EXPIRED`。后续 #t45 仍需补 mTLS、attestation、key rotation、持久化 Connector API 与 SDK。
