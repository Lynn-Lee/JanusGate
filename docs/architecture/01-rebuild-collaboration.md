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

## 3. 分工确认

### deepseek-architect 负责

1. 项目初始化：FastAPI 脚手架、`pyproject.toml`、`Dockerfile`、`docker-compose`。
2. 核心基础设施：SQLAlchemy 引擎、Alembic、Redis、配置模块、基础异常处理、健康检查。
3. 安全基座：AES-256-GCM、密码哈希、JWT 与黑名单、速率限制、CORS/Secure Cookie、安全响应头。
4. Identity & Auth：用户模型、登录、MFA、OAuth2/OIDC、API Key。
5. 威胁模型、依赖替换、安全验收 CI。

### tc-codex-architect 负责

1. 总体架构文档、阶段计划、任务拆分和 Git 协作规则。
2. PolicyDecisionService：策略模型、策略决策、explain、deny-by-default。
3. Connector API v2：注册、握手、心跳、短期连接 token、审计事件规范。
4. Credential Vault：SecretProvider 抽象、凭据引用、轮换接口、审计事件。
5. 评审 deepseek-architect 的安全基座是否满足后续策略/连接器/Vault 扩展点。

## 4. 第一阶段开发顺序

1. deepseek-architect 先提交项目脚手架和基础设施。
2. tc-codex-architect 拉取最新 `origin/dev`，在已有基础上提交架构文档和策略/连接器/Vault 的接口设计。
3. 双方分别在自己的 owner 目录内开发，避免并发改同一文件。
4. 每个可运行增量必须包含测试、文档和安全验收说明。
5. 阶段合并前，以 `docs/architecture/00-final-evaluation.md` 和本文件为验收依据。

## 5. 目录 owner 初始划分

| 路径 | Owner | 说明 |
|---|---|---|
| `src/janusgate/core/` | deepseek-architect | 配置、安全基础设施、异常、日志、依赖注入 |
| `src/janusgate/identity/` | deepseek-architect | 用户、认证、MFA、OIDC、API Key |
| `src/janusgate/policy/` | tc-codex-architect | PolicyDecisionService 和策略模型 |
| `src/janusgate/connectors/` | tc-codex-architect | Connector API v2 协议和注册握手 |
| `src/janusgate/vault/` | tc-codex-architect | SecretProvider 和凭据生命周期 |
| `docs/architecture/` | tc-codex-architect 主责，双方可 review | 架构、接口、阶段计划 |
| `docs/security/` | deepseek-architect 主责，双方可 review | 威胁模型、安全基线、验收门禁 |

## 6. 提交规范

- 文档：`docs: ...`
- 架构：`arch: ...`
- 功能：`feat: ...`
- 安全：`security: ...`
- 测试：`test: ...`
- 重构：`refactor: ...`
- CI：`ci: ...`

每次提交必须说明可验证结果；安全关键路径必须有测试或检查脚本。
