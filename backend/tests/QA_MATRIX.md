# JanusGate 风险测试矩阵

> 适用范围：`backend/` FastAPI 后端。矩阵用于指导 pytest 单元测试、集成测试、API 契约测试与安全回归测试的最小必测集。

## P0 发布阻断风险

| 模块/路径 | 用户风险 | 必测场景 | 验证方式 | 门禁 |
|---|---|---|---|---|
| `app/core/config.py` | 生产环境弱密钥或缺失密钥导致 token/加密失效 | `SECRET_KEY` 缺失、长度不足、合法值加载 | 单元测试 | 弱密钥必须 fail-closed |
| `app/core/security.py` | 密码、JWT、字段加密被绕过或不可逆损坏 | 密码策略、hash/verify、JWT 生命周期、access/refresh token 类型、过期 token、AES-GCM 加解密、AES-GCM 篡改密文、密文随机 nonce | 单元测试 + 安全回归 | 加密禁用 ECB；解密失败不得泄露异常细节 |
| `app/core/deps.py` | 未授权用户访问受保护 API | 无 token、非法 token、refresh token 误用、黑名单 token、缺少权限、timing-safe compare 正/反例 | 单元测试 + API 集成测试 | 所有失败路径返回 401/403，不能返回 500 |
| `api/auth/` | 登录、MFA、OAuth2/OIDC state 被绕过 | 登录成功/失败、MFA 必填、OAuth2 state mismatch、重复 state、回调错误 | API 契约 + 安全回归 | state mismatch 必须拒绝；token 生命周期必须可测 |
| `api/sessions/` | 会话越权、连接调度错误、资源泄露 | 创建/续租/终止会话、跨租户访问、资产不可用、并发限流 | API 契约 + 集成测试 | 跨租户访问必须拒绝；异常必须审计 |
| `api/audits/` | 操作不可追溯，SIEM 投递丢失 | 操作日志写入、命令记录、SIEM 投递成功/失败、重试/幂等 | 单元测试 + 集成测试 | 安全关键操作必须有审计事件 |
| `api/workflows/` | JIT 审批被绕过或审批状态错乱 | 创建工单、审批/拒绝、过期、重复审批、越权审批、审批后权限回收 | API 契约 + 集成测试 | 未审批或过期工单不得获得访问权限 |
| `services/policy/` | 策略误放行或误拒绝 | allow/deny、优先级、默认拒绝、缺失上下文、时间/资产/用户条件 | 单元测试 + API 集成测试 | 未匹配策略必须 deny by default |
| `connector/` | 连接器握手、token、审计事件协议不兼容 | 注册、握手、token 签发/刷新、审计事件 schema 校验 | API 契约测试 | 协议字段必须向后兼容或显式版本化 |
| `vault/` | 凭据泄露、轮换失败、密文不可恢复 | secret 创建/读取/轮换、权限校验、错误密钥、重复轮换 | 单元测试 + 集成测试 | 明文不得进入日志或响应 |
| `.github/workflows/`、`deploy/` | 质量门禁未执行或部署配置泄密 | CI lint/typecheck/test/security scan、Docker 构建、Helm values 无明文 secret、回滚说明 | CI 验证 + 配置审查 | PR 必须暴露质量检查结果，密钥只能走安全注入 |

## P1 高风险回归

| 风险面 | 必测场景 | 验证方式 | 触发条件 |
|---|---|---|---|
| 速率限制 | 登录爆破、全局请求限流、租户级隔离 | 集成测试 | 修改 `RATE_LIMIT_*`、auth、middleware |
| 输入校验 | SQL/命令注入载荷、超长字段、非法 UUID、非法枚举 | API 契约测试 | 新增/修改 API 入参 |
| 租户隔离 | 用户、资产、会话、审计事件跨租户访问 | 集成测试 | 涉及 tenant_id 查询或权限判断 |
| 错误处理 | 业务异常、校验异常、外部依赖异常 | 单元/API 测试 | 修改 exception handler 或外部集成 |
| 日志脱敏 | token、password、secret、private key 不进入日志 | 单元/静态检查 | 修改日志、审计、Vault、Auth |
| 数据库事务 | 成功提交、异常 rollback、连接关闭 | 单元/集成测试 | 修改 `database.py` 或 repository 层 |

## P2 基础可用性

| 场景 | 必测内容 | 验证方式 |
|---|---|---|
| 健康检查 | `/health` 返回 `status=ok` 和版本 | API smoke |
| OpenAPI 契约 | 开发环境暴露 docs，生产环境关闭 docs/redoc | API 契约 |
| CORS | 允许配置内来源，拒绝非配置来源 | API 集成 |
| 依赖注入 | DB/Redis/mock provider 可替换，测试不依赖真实外部服务 | 单元/fixture |

## 用例落地优先级

1. 先补 `core/security.py`、`core/config.py`、`core/deps.py` 的 P0 单元测试。
2. 再补 `/health` 与首批 auth/session/audit/policy API 的契约测试。
3. 每个新业务模块合入前，至少覆盖：成功路径、认证/授权失败、非法输入、默认拒绝/异常路径。
4. 安全路径（auth、crypto、token、Vault、policy deny）缺测试时不得放行发布。

## QA 与 Tester 分工

- QA（`tc-codex-qa-engineer`）：维护风险矩阵、覆盖率门禁、发布 go/no-go 标准和豁免要求；审核测试覆盖是否足以支撑发布。
- Tester（`codex-tester`）：落地 pytest 用例、测试环境拉起、API smoke、安全回归执行、缺陷复现与证据记录。
- 交界规则：QA 不重复执行常规测试；Tester 发现矩阵缺口时回填用例建议，QA 更新门禁或风险等级。
