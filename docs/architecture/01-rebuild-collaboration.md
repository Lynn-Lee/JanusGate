# JanusGate 重构协作分工与 Git 基线

> 状态：双方确认版  
> 日期：2026-06-29  
> 共享仓库：<https://github.com/Lynn-Lee/JanusGate>  
> 共享分支：`dev`

## 1. 协作原则

1. **Git 仓库是唯一共享事实源**：不同 agent 可能运行在不同服务器，本地文件不互通；所有可复用代码、文档、设计结论必须提交到 `origin/dev` 或约定分支。
2. **开始工作前必须先同步**：每次修改前执行 `git fetch origin && git pull --ff-only origin dev`。
3. **禁止本地口头约定替代仓库文档**：架构、接口、任务拆分、验收标准必须落到 `docs/` 或代码内测试。
4. **避免同文件并发修改**：同一阶段按目录和模块划分 owner；跨 owner 修改必须先在频道确认。
5. **不从 JumpServer 搬旧代码**：JumpServer 只作为 PRD、业务路径和迁移字段参考；JanusGate 架构、领域模型、安全模型和代码重新设计。

## 2. 当前技术栈决策

| 层面 | JanusGate 选择 | 说明 |
|---|---|---|
| 后端主框架 | FastAPI | 高 API 边界、异步连接、策略服务和连接器协议优先 |
| 语言版本 | Python 3.12/3.13+ | 不支持 Python 2/旧兼容包 |
| Schema | Pydantic v2 | 请求/响应/领域 DTO 明确建模 |
| ORM / Migration | SQLAlchemy 2.x + Alembic | 与 FastAPI 生态一致，迁移可控 |
| 数据库 | PostgreSQL | 主数据和审计索引基础 |
| 缓存/短期状态 | Redis | 限流、短期 token 状态；不存长期敏感凭据 |
| 前端 | React 19 + TypeScript + Vite + Ant Design | 企业后台生态成熟；不继承旧 jQuery/Bootstrap |
| 加密 | AES-256-GCM / ChaCha20-Poly1305 + KMS/Vault | 禁止 ECB |
| 连接器安全 | mTLS / 签名注册 / 短期凭证 | 禁止共享 BOOTSTRAP_TOKEN |
| 可观测 | OpenTelemetry + Prometheus + Loki/ELK | 从第一阶段接入 |
| 部署 | Docker Compose + Helm/K8s | 本地开发和生产部署分层 |

## 3. 全员分工

### 架构师

**mac-opencode-architect**
1. 项目初始化：FastAPI 脚手架、`pyproject.toml`、`Dockerfile`、`docker-compose` ✅
2. 核心基础设施：SQLAlchemy 引擎、Alembic、Redis、配置模块、异常处理、健康检查
3. 安全基座：AES-256-GCM、bcrypt、JWT + 黑名单、速率限制、CORS/Secure Cookie
4. Identity & Auth 模块：用户、登录、MFA、OAuth2/OIDC、API Key
5. Inventory 模块：资产、平台、协议、节点树

**tc-codex-architect**
1. 总体架构文档、阶段计划、任务拆分、协作规则 ✅
2. PolicyDecisionService：策略模型、决策引擎、explain、策略模拟
3. Connector API v2：注册、握手、心跳、短期连接 token、审计事件规范
4. Credential Vault：SecretProvider 抽象、envelope encryption、轮换接口

### 开发工程师

**codex-developer + tc-codex-developer**
1. Session Gateway：会话生命周期、连接调度、WebSocket 管理
2. 审计模块：操作日志、命令记录、会话录像元数据、SIEM 投递
3. 工单/审批流：Workflow & JIT 权限申请与审批
4. 通知模块：站内通知、邮件、飞书/钉钉/企微

### 代码审查

**tc-codex-code-reviewer + codex-code-reviewer**
1. 每个 PR merge 前必须 review
2. 重点审查：认证/授权路径、加密实现、SQL 注入防护、输入校验
3. 架构一致性检查（是否遵循 Bounded Context 边界）

### 测试与 QA

**codex-tester + tc-codex-qa-engineer**
1. 单元测试 + 集成测试编写
2. 安全回归测试（OAuth2 state、JWT 生命周期、速率限制、timing-safe）
3. API 契约测试（DRF TestClient 覆盖全部端点）
4. 测试覆盖率监控与门禁

### DevOps

**tc-codex-devops-engineer**
1. GitHub Actions CI/CD：lint、typecheck、test、bandit、pip-audit
2. Docker 镜像构建与推送
3. Kubernetes Helm chart + 部署文档
4. 开发/测试环境管理与健康监控

## 4. 第一阶段开发顺序

1. deepseek-architect 先提交项目脚手架和基础设施。
2. tc-codex-architect 拉取最新 `origin/dev`，在已有基础上提交架构文档和策略/连接器/Vault 的接口设计。
3. 双方分别在自己的 owner 目录内开发，避免并发改同一文件。
4. 每个可运行增量必须包含测试、文档和安全验收说明。
5. 阶段合并前，以 `docs/architecture/00-final-evaluation.md` 和本文件为验收依据。

## 5. 目录 owner 划分

| 路径 | Owner | 说明 |
|---|---|---|
| `backend/app/core/` | mac-opencode-architect | 配置、数据库、安全基础设施、依赖注入 |
| `backend/app/models/` | mac-opencode-architect | ORM 模型（User、Asset、Credential、Session、AuditEvent） |
| `backend/app/api/auth/` | mac-opencode-architect | Identity & Auth 路由 |
| `backend/app/api/assets/` | mac-opencode-architect | Inventory 路由 |
| `backend/app/services/policy/` | tc-codex-architect | PolicyDecisionService |
| `backend/app/services/connector/` | tc-codex-architect | Connector API v2 |
| `backend/app/services/vault/` | tc-codex-architect | SecretProvider |
| `backend/app/api/sessions/` | developer | Session Gateway 路由 |
| `backend/app/api/audits/` | developer | 审计路由 |
| `backend/app/api/workflows/` | developer | 工单/审批流路由 |
| `backend/app/services/notify/` | developer | 通知服务 |
| `backend/tests/` | tester + qa-engineer | 测试代码 |
| `deploy/` | devops-engineer | Helm chart、部署脚本 |
| `.github/workflows/` | devops-engineer | CI/CD 流水线 |
| `docs/architecture/` | tc-codex-architect 主责 | 架构、接口、阶段计划 |
| `docs/security/` | mac-opencode-architect 主责 | 威胁模型、安全基线、验收门禁 |

## 6. 提交规范

- 文档：`docs: ...`
- 架构：`arch: ...`
- 功能：`feat: ...`
- 安全：`security: ...`
- 测试：`test: ...`
- 重构：`refactor: ...`
- CI：`ci: ...`

每次提交必须说明可验证结果；安全关键路径必须有测试或检查脚本。
