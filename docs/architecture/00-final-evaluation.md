# JanusGate 重构项目最终版评估报告 v1.0

> 基线文档 — 基于 JumpServer dev 分支完整评估
> 共享仓库：https://github.com/Lynn-Lee/JanusGate
> 仓库路径：`docs/architecture/00-final-evaluation.md`
> 原远端：https://github.com/jumpserver/jumpserver.git
> 分支：`dev`
> 提交：`ec9e76e405d36f34023f3c672dcb0fdeec57f1d1`（2026-06-26）
> 联合确认人：deepseek-architect、tc-codex-architect
> 确认时间：2026-06-29

---

## 0. 最终确认记录

本报告为 JanusGate 重构项目的双方确认基线版本，已综合：

1. tc-codex-architect 的架构、产品边界、技术路线和重构方案评估。
2. deepseek-architect 的安全漏洞、依赖维护状态、代码可维护性和商业 PAM 能力差距评估。
3. 双方 review 后补充的关键共识：OAuth2 CORS/client_id/state 问题、Django 4.1 SQL 注入 CVE 硬门禁、438 Mixin / 100+ import * / 4 monkey-patch 等可维护性量化指标，以及策略引擎预留 JIT / SSH CA / WebHook 扩展点。

后续 JanusGate 的架构设计、技术选型、任务拆分、P0 安全基线和阶段路线图均以本文档为依据。

---

## 1. 最终结论

JumpServer 是一个功能完整、落地场景成熟的开源 PAM / 堡垒机项目，但不适合作为 JanusGate 的直接二次开发底座长期演进。

更合理路线是：以 JumpServer 为业务参考和迁移对象，重新设计 JanusGate 的产品边界、核心领域模型、安全基线、连接器协议、策略引擎和审计体系。

最终判断：
1. 可以借鉴 JumpServer 的业务域和用户路径
2. 不建议沿用其当前大单体架构
3. 不建议保留其当前安全默认值和连接器信任模型
4. 不建议继续沿用其配置/加密/权限/审计分散实现
5. JanusGate 应定位为"新一代策略驱动的 PAM / 零信任访问网关"

---

## 2. 问题分级

### P0：必须立即规避的问题

| # | 问题 | 证据位置 | 风险 |
|---|------|----------|------|
| 1 | SECRET_KEY / BOOTSTRAP_TOKEN 默认空字符串 | `apps/jumpserver/conf.py:186-187` | Django Session 签名可伪造、组件注册可伪造 |
| 2 | BOOTSTRAP_TOKEN 普通比较，存在 timing attack | `apps/common/permissions.py:51` | 时序攻击逐字节泄露核心密钥 |
| 3 | ALLOWED_HOSTS = ['*'] | `apps/jumpserver/settings/base.py:90` | Host Header 注入完全暴露 |
| 4 | Session/CSRF Secure Cookie 默认 False | `apps/jumpserver/conf.py:648-650` | Cookie 可通过 HTTP 明文传输 |
| 5 | Docker 全局关闭 SSH host key 校验并启用弱算法 | `Dockerfile:48` | 所有 SSH 连接暴露于 MITM + 降级攻击 |
| 6 | 配置和字段加密使用 SM4/AES ECB | `apps/jumpserver/conf.py:88-116`、`apps/common/utils/crypto.py:52-123` | 无 nonce、无认证，加密可被分析和篡改 |
| 7 | Celery 使用/接受 pickle 序列化 | `apps/jumpserver/settings/libs.py:197-200` | 反序列化 RCE 风险面 |
| 8 | TempToken 可暴力破解且缺少限流 | `apps/authentication/backends/token.py:17-18` | 可枚举 token 冒充任意用户 |
| 9 | OAuth2 回调 state 校验不完整 + metadata CORS:* 泄露 client_id | `apps/authentication/backends/oauth2/views.py:53-79`、`oauth2_provider/views.py:39,74` | 登录 CSRF 攻击 + 钓鱼攻击面 |
| 10 | SuperConnectionToken 对象级授权需专项审计 | `apps/authentication/api/connection_token.py:748-824` | 越权访问 |

### P1：重构第一阶段必须解决的问题

| # | 问题 | 影响 |
|---|------|------|
| 1 | 权限策略分散，缺少统一 PolicyDecisionService | RBAC/组织/资产授权/ACL/MFA/审批/连接 token 各自判断，无统一 explain |
| 2 | Core 大单体边界弱 | Web/API、WebSocket、Celery、连接器、报表、自动化任务共享同一套 app |
| 3 | 连接器共享单一 BOOTSTRAP_TOKEN | 任一组件被攻破影响全局 |
| 4 | 公开 API、AllowAny、csrf_exempt 分散 | 扫描到 csrf_exempt 13 处、AllowAny 或空权限 44 处 |
| 5 | LDAP 密码缓存到 Redis | `apps/users/models/user/_auth.py:279-306`，凭据治理风险 |
| 6 | 审计不可抵赖能力不足 | 缺少链式哈希、WORM/SIEM 投递和失败补偿 |
| 7 | 依赖版本严重落后 | Django 4.1 EOL（5 个未修复 CVE 含 2 个 SQL 注入）、adal/msrestazure/eventlet/ldap3 等 |
| 8 | 测试覆盖严重不足 | 9 个测试文件 vs 1292 个源文件（0.7%） |

### P2：中长期治理问题

| # | 问题 | 量化基准 |
|---|------|----------|
| 1 | 前后端分离不彻底 | 仍有旧静态资源（jQuery、Bootstrap）和 Django 模板；遗留 CryptoJS AES ECB |
| 2 | 配置中心 conf.py 过大 | 1116 行单一文件，575 行 defaults 字典 |
| 3 | 代码可维护性差 | 438 个 Mixin、100+ 处 import *、4 个全局 monkey-patch、14 处静默异常吞没、EncryptCharField max_length 畸变 |
| 4 | 巨型 migration | 57 个 migration 文件共 8400 行，最大 2223 行 |
| 5 | 商业 PAM 核心能力缺失 | JIT 权限、SSH CA、Webhook/API 集成、会话全文搜索、Vault 后端不完整 |
| 6 | 依赖管理 | 84% 依赖严格 Pin（==），5 个严重风险、8 个高危风险 |
| 7 | 产品边界过宽 | README 列出 11 个外部组件（6 个私有），缺少开源/企业版能力矩阵 |

---

## 3. JanusGate 目标定位

**面向企业和云原生环境的策略驱动 PAM / 零信任访问网关。**

核心叙事：不是单纯"阻断访问"，而是"让正确的人，在正确时间，以正确权限，经过正确审批和审计，安全连接正确资源"。

核心能力：
1. 身份统一认证
2. 策略驱动授权
3. 即时权限 JIT
4. Secret / Vault 治理
5. 多协议连接网关
6. 全链路不可抵赖审计
7. 连接器插件生态
8. 安全默认、云原生部署、可观测治理

---

## 4. 目标架构

### 4.1 分层架构

| 层 | 职责 | 组件 |
|----|------|------|
| Interface | 对外 API 入口 | REST API、WebSocket/Streaming API、Connector API、Webhook API、Admin API |
| Application Service | 业务用例编排 | AuthService、InventoryService、PolicyDecisionService、SessionOrchestrator、CredentialService、AuditService、WorkflowService |
| Domain | 核心领域模型 | User/Identity、Asset/Resource、Account/Credential、Policy/Rule、Session、AuditEvent、AccessRequest |
| Infrastructure | 基础支撑 | PostgreSQL、Redis/Queue、Object Storage、KMS/Vault、SIEM/Webhook、Connector Registry |

### 4.2 Bounded Context

1. **Identity & Auth** — 用户、组织、认证源、MFA、SSO、API Token
2. **Inventory** — 资产、节点、平台、协议、标签、云同步
3. **Credential Vault** — 账号、密钥、改密、推送、校验、轮换
4. **Policy & Permission** — RBAC、资产授权、ACL、条件访问、审批策略、策略模拟
5. **Session Gateway** — 连接 token、会话生命周期、连接器调度
6. **Audit & Compliance** — 登录、操作、命令、文件、录像、报表、SIEM
7. **Workflow & JIT** — 工单、审批、通知、即时权限
8. **Connector Platform** — 连接器注册、能力声明、版本兼容、SDK
9. **Automation Worker** — Ansible、扫描、改密等高风险任务独立队列

---

## 5. 核心设计原则

1. **安全默认**：生产环境必须 fail-closed
2. **策略中心化**：所有访问动作必须经过统一策略决策
3. **凭据最小暴露**：连接器只拿短时凭据或一次性 token
4. **连接器零信任**：每个连接器独立身份、独立密钥、独立授权范围
5. **审计不可抵赖**：审计事件追加写、链式哈希、外部投递
6. **模块化单体优先**：初期不盲目微服务化，但必须按领域边界设计
7. **可测试优先**：权限、token、secret、审计都必须有回归测试

---

## 6. 重构路线

### Phase 0：紧急安全基线（1 周）

目标：新项目立项时直接避开 JumpServer 的 P0 风险。

| # | 任务 |
|---|------|
| 1 | 强制 SECRET_KEY / bootstrap secret / DB secret 非空 |
| 2 | 所有 token 比较使用恒定时间比较（hmac.compare_digest） |
| 3 | Host 白名单显式配置 |
| 4 | Secure Cookie 默认开启 |
| 5 | 禁用全局 legacy SSH，改为资产级例外策略 |
| 6 | 禁止 ECB，默认 AEAD（AES-256-GCM / ChaCha20-Poly1305） |
| 7 | 禁止 pickle 任务序列化 |
| 8 | 登录、TempToken、验证码、OAuth 回调独立限流 |
| 9 | OAuth2 provider metadata 限制 CORS + 添加 state 参数验证 |

### Phase 1：产品边界与核心模型（2-3 周）

1. 定义 JanusGate 产品 PRD
2. 定义开源版 / 企业版能力边界
3. 定义核心领域模型：Identity、Resource、Credential、Policy、Session、AuditEvent
4. 输出 API 风格规范、错误码规范、审计事件规范

### Phase 2：策略引擎 MVP（4-6 周）

1. 实现 PolicyDecisionService
2. 支持 RBAC + ABAC + 时间窗口 + 来源 IP + MFA + 审批状态
3. 支持 explain（权限诊断）
4. 支持策略模拟
5. 建立权限矩阵测试框架
6. 预留 JIT 权限 / SSH CA / WebHook 扩展点

### Phase 3：Credential Vault 与 SecretProvider（4-6 周）

1. 设计 SecretProvider 接口
2. 支持 DBEncryptedProvider、KMSProvider、VaultProvider
3. 默认 envelope encryption
4. Secret 读取强审计
5. 凭据轮换、双写迁移、回滚

### Phase 4：Connector API v2（6-8 周）

1. Connector 注册和独立身份（API Key / 证书）
2. 能力声明
3. 版本兼容矩阵
4. 短时连接 token（绑定 user/asset/account/source/connector）
5. 连接前/连接中策略决策
6. 审计事件上报（标准 schema、签名、重试）
7. Connector SDK

### Phase 5：会话与审计（6-8 周）

1. SessionOrchestrator
2. 命令审计
3. 文件传输审计
4. 会话录像元数据管理
5. 审计链式哈希
6. SIEM/Webhook/WORM 投递
7. 审计失败补偿和告警

### Phase 6：产品增强（长期）

1. JIT 权限
2. SSH CA
3. 会话全文搜索
4. 动态审批流
5. CLI 客户端
6. 多租户 / 多组织简化模式
7. 安全运营 Dashboard

---

## 7. 技术选型建议

| 领域 | 建议 |
|------|------|
| 后端框架 | Python 3.12+、Django LTS 或 FastAPI + SQLAlchemy（需团队决策） |
| 数据库 | PostgreSQL 优先 |
| 消息队列 | Redis Streams / RabbitMQ / NATS（禁止 pickle） |
| 加密 | AES-256-GCM / ChaCha20-Poly1305；KMS/Vault 托管主密钥 |
| 策略引擎 | 初期自研 PolicyDecisionService；中后期评估 OPA/Rego |
| 前端 | Vue 3 / React，完全前后端分离 |
| 部署 | Docker Compose + Helm + Kubernetes |
| 可观测 | OpenTelemetry + Prometheus + Loki/ELK |
| CI/CD | ruff、mypy、pytest、bandit、pip-audit、semgrep、SBOM、镜像扫描 |

---

## 8. 最终建议

JanusGate 应避免变成 JumpServer 的"换皮重构"。建议采用：

- 业务能力参考 JumpServer
- 安全模型重新设计
- 策略系统重新设计
- 连接器协议重新设计
- Secret 和审计系统重新设计
- 代码架构重新设计

第一阶段最小闭环：

```
用户登录 → 策略决策 → 资产授权 → 短时连接 token
  → Connector 建立 SSH 会话 → 命令审计
  → 会话结束 → 审计事件不可抵赖落库
```

闭环打牢后，再逐步扩展 RDP、K8s、DB、JIT、SSH CA、Webhook 和企业版能力。
